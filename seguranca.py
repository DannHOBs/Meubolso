"""Segurança do OffBank: regras de senha e proteção dos backups.

A proteção do backup usa apenas a biblioteca padrão do Python (assim o APK não
depende de nenhum pacote extra), com peças conhecidas e combinadas da forma padrão:

  1. scrypt transforma a senha em duas chaves (uma para cifrar, outra para autenticar);
  2. HMAC-SHA256 em modo contador gera o fluxo de cifragem (cifra de fluxo);
  3. "cifrar e depois autenticar": uma etiqueta HMAC-SHA256 sobre cabeçalho + dados cifrados
     detecta senha errada ou arquivo alterado ANTES de qualquer descriptografia.

Formato do arquivo .mbk:
  MAGIA | log2(n) | r | p | sal (16) | nonce (16) | dados cifrados | etiqueta (32)
"""

import hashlib
import hmac
import os

SENHA_MINIMA = 8

MAGIA = b"OFFBANK-BK1\n"
# Backups feitos quando o app ainda se chamava Meu Bolso continuam abrindo.
MAGIA_ANTIGA = b"MEUBOLSO-BK1\n"
TAM_SAL = 16
TAM_NONCE = 16
TAM_ETIQUETA = 32

# Custo do scrypt (~32 MB de memória): deixa cada tentativa de adivinhar a senha bem lenta.
N_LOG2 = 15
R = 8
P = 1


class SenhaOuArquivoInvalido(Exception):
    """Senha errada, arquivo danificado ou arquivo que não é um backup protegido."""


# ==========================================
# REGRAS DE SENHA
# ==========================================

_SEQUENCIAS = (
    "0123456789",
    "9876543210",
    "abcdefghijklmnopqrstuvwxyz",
    "zyxwvutsrqponmlkjihgfedcba",
    "qwertyuiopasdfghjklzxcvbnm",
    "mnbvcxzlkjhgfdsapoiuytrewq",
)

_COMUNS = {
    "password", "senha123", "senha1234", "senha12345", "senhasenha",
    "brasil123", "brasil1234", "mudar123", "admin123", "abc12345",
    "123123123", "12341234", "11223344", "offbank", "offbank1", "meubolso", "meubolso1",
}


def validar_senha(senha):
    """Devolve uma mensagem de erro se a senha for fraca, ou None se estiver boa."""
    if len(senha) < SENHA_MINIMA:
        return f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres."

    minuscula = senha.lower()

    if len(set(senha)) == 1 or any(minuscula in seq for seq in _SEQUENCIAS) or minuscula in _COMUNS:
        return "Essa senha é fácil de adivinhar. Misture letras e números, ou use uma frase curta."

    return None


# ==========================================
# CRIPTOGRAFIA DO BACKUP
# ==========================================

def _derivar_chaves(senha, sal, n_log2, r, p):
    chave = hashlib.scrypt(
        senha.encode("utf-8"),
        salt=sal,
        n=1 << n_log2,
        r=r,
        p=p,
        maxmem=256 * 1024 * 1024,
        dklen=64,
    )
    return chave[:32], chave[32:]


def _fluxo(chave_cifra, nonce, tamanho):
    """Sequência pseudoaleatória de 'tamanho' bytes: HMAC(chave, nonce + contador)."""
    base = hmac.new(chave_cifra, nonce, hashlib.sha256)
    blocos = []

    for contador in range((tamanho + 31) // 32):
        bloco = base.copy()
        bloco.update(contador.to_bytes(8, "big"))
        blocos.append(bloco.digest())

    return b"".join(blocos)[:tamanho]


def _xor(dados, fluxo):
    if not dados:
        return b""

    resultado = int.from_bytes(dados, "big") ^ int.from_bytes(fluxo, "big")
    return resultado.to_bytes(len(dados), "big")


def _magia_do_arquivo(inicio_do_arquivo):
    """Devolve o cabeçalho que o arquivo usa (novo ou antigo), ou None se não for protegido."""
    for magia in (MAGIA, MAGIA_ANTIGA):
        if inicio_do_arquivo.startswith(magia):
            return magia
    return None


def eh_arquivo_protegido(inicio_do_arquivo):
    return _magia_do_arquivo(inicio_do_arquivo) is not None


def criptografar(dados, senha):
    """Devolve os bytes do arquivo .mbk para os 'dados' protegidos pela 'senha'."""
    sal = os.urandom(TAM_SAL)
    nonce = os.urandom(TAM_NONCE)
    cabecalho = MAGIA + bytes([N_LOG2, R, P]) + sal + nonce

    chave_cifra, chave_etiqueta = _derivar_chaves(senha, sal, N_LOG2, R, P)
    cifrado = _xor(dados, _fluxo(chave_cifra, nonce, len(dados)))
    etiqueta = hmac.new(chave_etiqueta, cabecalho + cifrado, hashlib.sha256).digest()

    return cabecalho + cifrado + etiqueta


def descriptografar(conteudo, senha):
    """Devolve os dados originais. Levanta SenhaOuArquivoInvalido se não for possível."""
    magia = _magia_do_arquivo(conteudo) or MAGIA
    tamanho_cabecalho = len(magia) + 3 + TAM_SAL + TAM_NONCE

    if len(conteudo) < tamanho_cabecalho + TAM_ETIQUETA or not eh_arquivo_protegido(conteudo):
        raise SenhaOuArquivoInvalido("Esse arquivo não é um backup protegido do OffBank.")

    n_log2, r, p = conteudo[len(magia)], conteudo[len(magia) + 1], conteudo[len(magia) + 2]

    # Só aceita custos razoáveis (um arquivo malicioso não pode travar o celular).
    if not (14 <= n_log2 <= 16 and r == 8 and p == 1):
        raise SenhaOuArquivoInvalido("O arquivo de backup está danificado.")

    inicio_sal = len(magia) + 3
    sal = conteudo[inicio_sal:inicio_sal + TAM_SAL]
    nonce = conteudo[inicio_sal + TAM_SAL:tamanho_cabecalho]
    cabecalho = conteudo[:tamanho_cabecalho]
    cifrado = conteudo[tamanho_cabecalho:-TAM_ETIQUETA]
    etiqueta = conteudo[-TAM_ETIQUETA:]

    chave_cifra, chave_etiqueta = _derivar_chaves(senha, sal, n_log2, r, p)
    esperada = hmac.new(chave_etiqueta, cabecalho + cifrado, hashlib.sha256).digest()

    if not hmac.compare_digest(etiqueta, esperada):
        raise SenhaOuArquivoInvalido("Senha do backup incorreta (ou o arquivo foi alterado).")

    return _xor(cifrado, _fluxo(chave_cifra, nonce, len(cifrado)))
