package br.com.meubolso;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.KeyguardManager;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.URL;
import java.security.SecureRandom;

/**
 * Tela única do Meu Bolso: sobe o servidor Flask (Python, via Chaquopy)
 * em 127.0.0.1 e mostra o sistema dentro de um WebView.
 */
public class MainActivity extends Activity {

    private static volatile int porta = 0;
    private static volatile boolean servidorPronto = false;

    private WebView webView;
    private TextView aviso;
    private String enderecoBase;

    private static final int PEDIDO_ARQUIVO = 1001;
    private static final int PEDIDO_BLOQUEIO = 1002;

    // Código secreto desta execução do app (compartilhado só com o servidor Python).
    private static volatile String tokenRapido = "";
    private ValueCallback<Uri[]> seletorArquivo;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        FrameLayout raiz = new FrameLayout(this);

        webView = new WebView(this);
        webView.setVisibility(View.INVISIBLE);
        raiz.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        aviso = new TextView(this);
        aviso.setText("Iniciando o Meu Bolso...");
        aviso.setTextSize(18);
        aviso.setGravity(Gravity.CENTER);
        aviso.setPadding(48, 48, 48, 48);
        raiz.addView(aviso, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(raiz);

        configurarWebView();

        // Pede a senha novamente a cada abertura do aplicativo.
        CookieManager.getInstance().removeAllCookies(null);

        iniciarServidor();
    }

    private void configurarWebView() {
        WebSettings ajustes = webView.getSettings();
        ajustes.setJavaScriptEnabled(true);
        ajustes.setDomStorageEnabled(true);
        ajustes.setAllowFileAccess(false);
        ajustes.setAllowContentAccess(false);

        // Ponte usada pela tela de login para pedir o bloqueio do celular (desbloqueio rápido).
        webView.addJavascriptInterface(new PonteAndroid(), "MeuBolsoAndroid");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                // Nunca sai do sistema local.
                return enderecoBase == null || !url.startsWith(enderecoBase);
            }
        });

        // Sem isso, os avisos de confirmação (excluir, restaurar...) não aparecem.
        webView.setWebChromeClient(new WebChromeClient() {
            // Necessário para escolher o arquivo de backup na tela Backup > Restaurar.
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (seletorArquivo != null) {
                    seletorArquivo.onReceiveValue(null);
                }
                seletorArquivo = callback;

                try {
                    startActivityForResult(params.createIntent(), PEDIDO_ARQUIVO);
                } catch (ActivityNotFoundException semSeletor) {
                    seletorArquivo = null;
                    return false;
                }
                return true;
            }

            @Override
            public boolean onJsAlert(WebView view, String url, String message, final JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton("OK", (dialogo, botao) -> result.confirm())
                        .setOnCancelListener(dialogo -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, final JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton("OK", (dialogo, botao) -> result.confirm())
                        .setNegativeButton("Cancelar", (dialogo, botao) -> result.cancel())
                        .setOnCancelListener(dialogo -> result.cancel())
                        .show();
                return true;
            }
        });

        // PDF do relatório e cópias de backup.
        webView.setDownloadListener((url, userAgent, contentDisposition, mimetype, contentLength) ->
                baixarArquivo(url, contentDisposition, mimetype));
    }

    private void iniciarServidor() {
        if (servidorPronto) {
            carregarSistema();
            return;
        }

        new Thread(() -> {
            try {
                int portaEscolhida = escolherPortaLivre();

                File dados = new File(getFilesDir(), "dados");
                File templates = new File(getFilesDir(), "web/templates");

                copiarAssets("templates", templates);

                String token = gerarToken();

                Python.getInstance()
                        .getModule("android_main")
                        .callAttr("iniciar", dados.getAbsolutePath(), templates.getAbsolutePath(), portaEscolhida, token);

                tokenRapido = token;

                porta = portaEscolhida;
                servidorPronto = true;

                runOnUiThread(this::carregarSistema);
            } catch (final Throwable erro) {
                runOnUiThread(() -> mostrarErro(erro));
            }
        }).start();
    }

    private void carregarSistema() {
        enderecoBase = "http://127.0.0.1:" + porta + "/";
        aviso.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(enderecoBase);
    }

    private void mostrarErro(Throwable erro) {
        aviso.setVisibility(View.VISIBLE);
        aviso.setText("Não foi possível iniciar o sistema.\n\n" + erro);
    }

    private int escolherPortaLivre() throws IOException {
        try (ServerSocket socket = new ServerSocket(0, 0, InetAddress.getByName("127.0.0.1"))) {
            return socket.getLocalPort();
        }
    }

    // ---------- cópia das telas (HTML) para a pasta do app ----------

    private void copiarAssets(String origem, File destino) throws IOException {
        String[] itens = getAssets().list(origem);

        if (itens == null) {
            return;
        }

        if (itens.length == 0) {
            copiarArquivoAsset(origem, destino);
            return;
        }

        if (!destino.exists() && !destino.mkdirs()) {
            throw new IOException("Não foi possível criar a pasta " + destino);
        }

        for (String item : itens) {
            copiarAssets(origem + "/" + item, new File(destino, item));
        }
    }

    private void copiarArquivoAsset(String origem, File destino) throws IOException {
        try (InputStream entrada = getAssets().open(origem);
             OutputStream saida = new FileOutputStream(destino)) {
            byte[] buffer = new byte[8192];
            int lidos;
            while ((lidos = entrada.read(buffer)) != -1) {
                saida.write(buffer, 0, lidos);
            }
        }
    }

    // ---------- downloads (PDF e backups) ----------

    private void baixarArquivo(final String url, final String contentDisposition, final String mimetype) {
        final String cookie = CookieManager.getInstance().getCookie(url);
        final String nome = URLUtil.guessFileName(url, contentDisposition, mimetype);
        final String tipoArquivo = (mimetype != null && !mimetype.isEmpty())
                ? mimetype
                : "application/octet-stream";

        Toast.makeText(this, "Baixando " + nome + "...", Toast.LENGTH_SHORT).show();

        new Thread(() -> {
            try {
                HttpURLConnection conexao = (HttpURLConnection) new URL(url).openConnection();
                conexao.setConnectTimeout(15000);
                conexao.setReadTimeout(60000);

                if (cookie != null) {
                    conexao.setRequestProperty("Cookie", cookie);
                }

                if (conexao.getResponseCode() != 200) {
                    throw new IOException("O servidor respondeu " + conexao.getResponseCode());
                }

                ContentValues valores = new ContentValues();
                valores.put(MediaStore.Downloads.DISPLAY_NAME, nome);
                valores.put(MediaStore.Downloads.MIME_TYPE, tipoArquivo);
                valores.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/MeuBolso");
                valores.put(MediaStore.Downloads.IS_PENDING, 1);

                final Uri destino = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, valores);

                if (destino == null) {
                    throw new IOException("Não foi possível criar o arquivo em Downloads.");
                }

                try (InputStream entrada = conexao.getInputStream();
                     OutputStream saida = getContentResolver().openOutputStream(destino)) {
                    byte[] buffer = new byte[8192];
                    int lidos;
                    while ((lidos = entrada.read(buffer)) != -1) {
                        saida.write(buffer, 0, lidos);
                    }
                }

                valores.clear();
                valores.put(MediaStore.Downloads.IS_PENDING, 0);
                getContentResolver().update(destino, valores, null, null);

                runOnUiThread(() -> arquivoSalvo(destino, nome, tipoArquivo));
            } catch (final Exception erro) {
                runOnUiThread(() -> Toast.makeText(
                        MainActivity.this,
                        "Erro ao baixar: " + erro.getMessage(),
                        Toast.LENGTH_LONG).show());
            }
        }).start();
    }

    private void arquivoSalvo(Uri arquivo, String nome, String tipo) {
        if ("application/pdf".equalsIgnoreCase(tipo)) {
            Intent abrir = new Intent(Intent.ACTION_VIEW);
            abrir.setDataAndType(arquivo, "application/pdf");
            abrir.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);

            try {
                startActivity(abrir);
                return;
            } catch (ActivityNotFoundException semLeitor) {
                // Sem leitor de PDF: cai no aviso abaixo.
            }
        }

        Toast.makeText(
                this,
                "Arquivo salvo em Downloads/MeuBolso: " + nome,
                Toast.LENGTH_LONG).show();
    }

    // ---------- desbloqueio pelo bloqueio do celular ----------

    private static String gerarToken() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);

        StringBuilder texto = new StringBuilder();
        for (byte b : bytes) {
            texto.append(String.format("%02x", b));
        }
        return texto.toString();
    }

    /** Funções que a página de login (HTML) pode chamar. Só páginas do próprio app são carregadas aqui. */
    private class PonteAndroid {

        @JavascriptInterface
        public boolean disponivel() {
            KeyguardManager gerenciador = (KeyguardManager) getSystemService(Context.KEYGUARD_SERVICE);
            return gerenciador != null && gerenciador.isDeviceSecure();
        }

        @JavascriptInterface
        public void autenticar() {
            runOnUiThread(() -> pedirBloqueioDoCelular());
        }
    }

    private void pedirBloqueioDoCelular() {
        KeyguardManager gerenciador = (KeyguardManager) getSystemService(Context.KEYGUARD_SERVICE);

        if (gerenciador == null || !gerenciador.isDeviceSecure()) {
            Toast.makeText(this, "Configure um bloqueio de tela no celular para usar esta opção.", Toast.LENGTH_LONG).show();
            return;
        }

        Intent pedido = gerenciador.createConfirmDeviceCredentialIntent(
                "Meu Bolso", "Confirme que é você para abrir o app");

        if (pedido == null) {
            Toast.makeText(this, "Não foi possível pedir o bloqueio do celular.", Toast.LENGTH_LONG).show();
            return;
        }

        try {
            startActivityForResult(pedido, PEDIDO_BLOQUEIO);
        } catch (ActivityNotFoundException semTela) {
            Toast.makeText(this, "Não foi possível pedir o bloqueio do celular.", Toast.LENGTH_LONG).show();
        }
    }

    // ---------- resultado do seletor de arquivos e do bloqueio ----------

    @Override
    protected void onActivityResult(int pedido, int resultado, Intent dados) {
        if (pedido == PEDIDO_BLOQUEIO) {
            if (resultado == RESULT_OK && enderecoBase != null && !tokenRapido.isEmpty()) {
                webView.loadUrl(enderecoBase + "entrada-rapida?t=" + tokenRapido);
            }
            return;
        }

        if (pedido == PEDIDO_ARQUIVO && seletorArquivo != null) {
            seletorArquivo.onReceiveValue(
                    WebChromeClient.FileChooserParams.parseResult(resultado, dados));
            seletorArquivo = null;
            return;
        }
        super.onActivityResult(pedido, resultado, dados);
    }

    // ---------- botão voltar ----------

    @Override
    public void onBackPressed() {
        if (webView.getVisibility() == View.VISIBLE && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
