package br.com.offbank;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.KeyguardManager;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.SystemClock;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
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
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;

import java.io.File;
import java.io.FileInputStream;
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
 * Tela única do OffBank: sobe o servidor Flask (Python, via Chaquopy)
 * em 127.0.0.1 e mostra o sistema dentro de um WebView.
 */
public class MainActivity extends Activity {

    private static volatile int porta = 0;
    private static volatile boolean servidorPronto = false;

    private WebView webView;
    private View telaAbertura;
    private ProgressBar carregando;
    private TextView aviso;
    private String enderecoBase;

    private static final int PEDIDO_ARQUIVO = 1001;
    private static final int PEDIDO_BLOQUEIO = 1002;
    private static final int PEDIDO_RECUPERAR_SENHA = 1003;

    // Fora do app por mais que isso (outro app, tela apagada), pede a senha de novo.
    private static final long BLOQUEIO_EM_SEGUNDO_PLANO_MS = 60_000;

    // Código secreto desta execução do app (compartilhado só com o servidor Python).
    private static volatile String tokenRapido = "";
    private ValueCallback<Uri[]> seletorArquivo;

    private boolean primeiraTelaCarregada = false;
    private boolean limparHistorico = false;
    private boolean aguardandoOutraTela = false;  // seletor de arquivo ou bloqueio do celular abertos por nós
    private long saiuEm = 0;

    private static final Object TRAVA_EXPORTACAO = new Object();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Valores financeiros: sem prints e sem miniatura na lista de apps recentes.
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);

        FrameLayout raiz = new FrameLayout(this);

        webView = new WebView(this);
        webView.setVisibility(View.INVISIBLE);
        raiz.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        telaAbertura = criarTelaAbertura();
        raiz.addView(telaAbertura, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(raiz);

        configurarWebView();

        // Pede a senha novamente a cada abertura do aplicativo.
        CookieManager.getInstance().removeAllCookies(null);

        iniciarServidor();
    }

    // ---------- tela de abertura (enquanto o Python inicia) ----------

    private View criarTelaAbertura() {
        LinearLayout tela = new LinearLayout(this);
        tela.setOrientation(LinearLayout.VERTICAL);
        tela.setGravity(Gravity.CENTER);
        tela.setBackground(new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[] {0xFF4338CA, 0xFF6366F1}));
        // Toques não passam para o WebView que está por baixo.
        tela.setClickable(true);

        ImageView icone = new ImageView(this);
        icone.setImageResource(R.drawable.ic_launcher_foreground);
        tela.addView(icone, new LinearLayout.LayoutParams(dp(160), dp(160)));

        TextView nome = new TextView(this);
        nome.setText("OffBank");
        nome.setTextColor(Color.WHITE);
        nome.setTextSize(28);
        nome.setTypeface(Typeface.DEFAULT_BOLD);
        nome.setGravity(Gravity.CENTER);
        tela.addView(nome, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        carregando = new ProgressBar(this);
        carregando.setIndeterminateTintList(ColorStateList.valueOf(Color.WHITE));
        LinearLayout.LayoutParams posicaoCarregando = new LinearLayout.LayoutParams(dp(34), dp(34));
        posicaoCarregando.topMargin = dp(32);
        tela.addView(carregando, posicaoCarregando);

        aviso = new TextView(this);
        aviso.setText("Preparando seus dados...");
        aviso.setTextColor(0xE6FFFFFF);
        aviso.setTextSize(15);
        aviso.setGravity(Gravity.CENTER);
        aviso.setPadding(dp(32), 0, dp(32), 0);
        LinearLayout.LayoutParams posicaoAviso = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        posicaoAviso.topMargin = dp(14);
        tela.addView(aviso, posicaoAviso);

        return tela;
    }

    private void esconderTelaAbertura() {
        webView.setVisibility(View.VISIBLE);
        telaAbertura.animate()
                .alpha(0f)
                .setDuration(250)
                .withEndAction(() -> telaAbertura.setVisibility(View.GONE));
    }

    private int dp(int valor) {
        return Math.round(valor * getResources().getDisplayMetrics().density);
    }

    private void configurarWebView() {
        WebSettings ajustes = webView.getSettings();
        ajustes.setJavaScriptEnabled(true);
        ajustes.setDomStorageEnabled(true);
        ajustes.setAllowFileAccess(false);
        ajustes.setAllowContentAccess(false);

        // Ponte usada pela tela de login para pedir o bloqueio do celular (desbloqueio rápido).
        webView.addJavascriptInterface(new PonteAndroid(), "OffBankAndroid");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                // Nunca sai do sistema local.
                return enderecoBase == null || !url.startsWith(enderecoBase);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (limparHistorico) {
                    // Depois de bloquear, o botão voltar não leva às telas de antes.
                    view.clearHistory();
                    limparHistorico = false;
                }

                if (!primeiraTelaCarregada) {
                    primeiraTelaCarregada = true;
                    esconderTelaAbertura();
                } else {
                    view.setVisibility(View.VISIBLE);
                }

                exportarBackupsAutomaticos();
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
                    aguardandoOutraTela = true;
                    startActivityForResult(params.createIntent(), PEDIDO_ARQUIVO);
                } catch (ActivityNotFoundException semSeletor) {
                    aguardandoOutraTela = false;
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
        // A tela de abertura só sai quando a primeira página terminar de carregar (onPageFinished).
        webView.loadUrl(enderecoBase);
    }

    private void mostrarErro(Throwable erro) {
        carregando.setVisibility(View.GONE);
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

                final Uri destino;
                try (InputStream entrada = conexao.getInputStream()) {
                    destino = salvarEmDownloads(nome, tipoArquivo, entrada);
                }

                runOnUiThread(() -> arquivoSalvo(destino, nome, tipoArquivo));
            } catch (final Exception erro) {
                runOnUiThread(() -> Toast.makeText(
                        MainActivity.this,
                        "Erro ao baixar: " + erro.getMessage(),
                        Toast.LENGTH_LONG).show());
            }
        }).start();
    }

    /** Grava em Downloads/OffBank (fica no celular mesmo se o app for desinstalado). */
    private Uri salvarEmDownloads(String nome, String tipo, InputStream entrada) throws IOException {
        ContentValues valores = new ContentValues();
        valores.put(MediaStore.Downloads.DISPLAY_NAME, nome);
        valores.put(MediaStore.Downloads.MIME_TYPE, tipo);
        valores.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/OffBank");
        valores.put(MediaStore.Downloads.IS_PENDING, 1);

        Uri destino = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, valores);

        if (destino == null) {
            throw new IOException("Não foi possível criar o arquivo em Downloads.");
        }

        try (OutputStream saida = getContentResolver().openOutputStream(destino)) {
            byte[] buffer = new byte[8192];
            int lidos;
            while ((lidos = entrada.read(buffer)) != -1) {
                saida.write(buffer, 0, lidos);
            }
        } catch (IOException erro) {
            getContentResolver().delete(destino, null, null);
            throw erro;
        }

        valores.clear();
        valores.put(MediaStore.Downloads.IS_PENDING, 0);
        getContentResolver().update(destino, valores, null, null);

        return destino;
    }

    /**
     * O servidor Python cria o backup automático semanal (backup_auto_*.mbk) na pasta do app.
     * Aqui ele é copiado para Downloads/OffBank; o marcador ".exportado" evita copiar duas vezes.
     */
    private void exportarBackupsAutomaticos() {
        final File pasta = new File(getFilesDir(), "dados/backups");

        new Thread(() -> {
            synchronized (TRAVA_EXPORTACAO) {
                File[] arquivos = pasta.listFiles((dir, nome) ->
                        nome.startsWith("backup_auto_") && nome.endsWith(".mbk"));

                if (arquivos == null) {
                    return;
                }

                for (File arquivo : arquivos) {
                    File marcador = new File(pasta, arquivo.getName() + ".exportado");

                    if (marcador.exists()) {
                        continue;
                    }

                    try (InputStream entrada = new FileInputStream(arquivo)) {
                        salvarEmDownloads(arquivo.getName(), "application/octet-stream", entrada);

                        if (!marcador.createNewFile()) {
                            continue;
                        }

                        runOnUiThread(() -> Toast.makeText(
                                MainActivity.this,
                                "Backup automático salvo em Downloads/OffBank",
                                Toast.LENGTH_LONG).show());
                    } catch (IOException erro) {
                        // Tenta de novo na próxima tela carregada.
                    }
                }
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
                "Arquivo salvo em Downloads/OffBank: " + nome,
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
            runOnUiThread(() -> pedirBloqueioDoCelular(PEDIDO_BLOQUEIO, "Confirme que é você para abrir o app"));
        }

        @JavascriptInterface
        public void recuperarSenha() {
            runOnUiThread(() -> pedirBloqueioDoCelular(PEDIDO_RECUPERAR_SENHA, "Confirme que é você para criar uma senha nova"));
        }
    }

    private void pedirBloqueioDoCelular(int codigoPedido, String descricao) {
        KeyguardManager gerenciador = (KeyguardManager) getSystemService(Context.KEYGUARD_SERVICE);

        if (gerenciador == null || !gerenciador.isDeviceSecure()) {
            Toast.makeText(this, "Configure um bloqueio de tela no celular para usar esta opção.", Toast.LENGTH_LONG).show();
            return;
        }

        Intent pedido = gerenciador.createConfirmDeviceCredentialIntent("OffBank", descricao);

        if (pedido == null) {
            Toast.makeText(this, "Não foi possível pedir o bloqueio do celular.", Toast.LENGTH_LONG).show();
            return;
        }

        try {
            aguardandoOutraTela = true;
            startActivityForResult(pedido, codigoPedido);
        } catch (ActivityNotFoundException semTela) {
            aguardandoOutraTela = false;
            Toast.makeText(this, "Não foi possível pedir o bloqueio do celular.", Toast.LENGTH_LONG).show();
        }
    }

    // ---------- bloqueio quando o app fica em segundo plano ----------

    @Override
    protected void onStop() {
        super.onStop();
        // O seletor de arquivo e o bloqueio do celular também "tiram" o app da tela: esses não contam.
        saiuEm = aguardandoOutraTela ? 0 : SystemClock.elapsedRealtime();
    }

    @Override
    protected void onStart() {
        super.onStart();

        if (saiuEm > 0 && servidorPronto && enderecoBase != null
                && SystemClock.elapsedRealtime() - saiuEm > BLOQUEIO_EM_SEGUNDO_PLANO_MS) {
            bloquear();
        }
        saiuEm = 0;
    }

    private void bloquear() {
        // Esconde a tela antiga na hora; ela volta quando a página de senha terminar de carregar.
        webView.setVisibility(View.INVISIBLE);

        // Assim a tela de senha volta a pedir a digital sozinha (se o desbloqueio rápido estiver ligado).
        webView.evaluateJavascript("try { sessionStorage.removeItem('rapido_tentado'); } catch (e) {}", null);

        // A sessão do Flask fica no cookie: apagando-o, o servidor pede a senha de novo.
        CookieManager.getInstance().removeAllCookies(removidos -> {
            limparHistorico = true;
            webView.loadUrl(enderecoBase);
        });
    }

    // ---------- resultado do seletor de arquivos e do bloqueio ----------

    @Override
    protected void onActivityResult(int pedido, int resultado, Intent dados) {
        aguardandoOutraTela = false;

        if (pedido == PEDIDO_BLOQUEIO) {
            if (resultado == RESULT_OK && enderecoBase != null && !tokenRapido.isEmpty()) {
                webView.loadUrl(enderecoBase + "entrada-rapida?t=" + tokenRapido);
            }
            return;
        }

        if (pedido == PEDIDO_RECUPERAR_SENHA) {
            if (resultado == RESULT_OK && enderecoBase != null && !tokenRapido.isEmpty()) {
                webView.loadUrl(enderecoBase + "recuperar-senha?t=" + tokenRapido);
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
