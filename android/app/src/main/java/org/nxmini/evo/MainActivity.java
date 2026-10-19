package org.nxmini.evo;

import android.app.Activity;
import android.content.ClipData;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.text.InputType;
import android.view.View;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.*;

/** NAS receiver -> phone original files -> Android Sharesheet / Quick Share. */
public class MainActivity extends Activity {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private EditText address, token;
    private LinearLayout list;
    private TextView status;
    private Button connect, nextPage;
    private String activeBase, activeToken;
    private Long pageCursor;
    private int displayed;

    private int dp(int value) { return Math.round(getResources().getDisplayMetrics().density * value); }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(20), dp(20), dp(16));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(dp(20) + insets.getSystemWindowInsetLeft(), dp(20) + insets.getSystemWindowInsetTop(),
                    dp(20) + insets.getSystemWindowInsetRight(), dp(16) + insets.getSystemWindowInsetBottom());
            return insets;
        });
        TextView title = new TextView(this);
        title.setText("NX mini Bridge"); title.setTextSize(26); root.addView(title);
        TextView intro = new TextView(this);
        intro.setText("从家庭接收端保存原图，再通过系统分享发送。请先让相机完成备份。");
        intro.setPadding(0, dp(8), 0, dp(16)); root.addView(intro);
        SharedPreferences prefs = getSharedPreferences("receiver", MODE_PRIVATE);
        address = new EditText(this);
        address.setSingleLine(true); address.setHint("接收端地址，例如 http://192.168.1.10:52235");
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setText(prefs.getString("address", "")); root.addView(address);
        token = new EditText(this); token.setSingleLine(true); token.setHint("访问令牌");
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        token.setText(prefs.getString("token", "")); root.addView(token);
        connect = new Button(this); connect.setText("连接并刷新照片"); root.addView(connect);
        nextPage = new Button(this); nextPage.setText("加载较早的照片"); nextPage.setEnabled(false); root.addView(nextPage);
        status = new TextView(this); status.setPadding(0, dp(10), 0, dp(10)); root.addView(status);
        ScrollView scroll = new ScrollView(this);
        list = new LinearLayout(this); list.setOrientation(LinearLayout.VERTICAL); scroll.addView(list);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root); root.requestApplyInsets();
        connect.setOnClickListener(v -> refresh(false));
        nextPage.setOnClickListener(v -> refresh(true));
    }

    private void ui(Runnable work) {
        runOnUiThread(() -> { if (!isDestroyed() && !isFinishing()) work.run(); });
    }

    private HttpURLConnection connection(String base, String path, String secret) throws IOException {
        HttpURLConnection request = (HttpURLConnection) new URL(base + path).openConnection();
        request.setConnectTimeout(10000); request.setReadTimeout(30000);
        request.setInstanceFollowRedirects(false);
        request.setRequestProperty("Authorization", "Bearer " + secret);
        if (request.getResponseCode() != 200) {
            int code = request.getResponseCode(); request.disconnect();
            throw new IOException(code == 401 ? "访问令牌不正确" : "接收端返回 HTTP " + code);
        }
        return request;
    }

    private void refresh(boolean older) {
        final String base = older ? activeBase : address.getText().toString().trim().replaceAll("/+$", "");
        final String secret = older ? activeToken : token.getText().toString().trim();
        final String page = older && pageCursor != null ? "?before=" + pageCursor : "";
        try {
            URI uri = new URI(base);
            if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null ||
                    uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null || secret.isEmpty()) {
                throw new Exception("请填写有效接收端地址和访问令牌");
            }
        } catch (Exception error) { status.setText("请填写有效接收端地址和访问令牌"); return; }
        connect.setEnabled(false); nextPage.setEnabled(false); status.setText("正在连接…");
        if (!older) { list.removeAllViews(); displayed = 0; pageCursor = null; }
        worker.execute(() -> {
            HttpURLConnection request = null;
            try {
                request = connection(base, "/api/files" + page, secret);
                ByteArrayOutputStream body = new ByteArrayOutputStream();
                try (InputStream source = request.getInputStream()) {
                    byte[] buffer = new byte[8192]; int count;
                    while ((count = source.read(buffer)) != -1) {
                        if (body.size() + count > 4 * 1024 * 1024) throw new IOException("照片列表过大");
                        body.write(buffer, 0, count);
                    }
                }
                JSONObject response = new JSONObject(body.toString("UTF-8"));
                JSONArray files = response.getJSONArray("files");
                Long cursor = response.isNull("next_before") ? null : response.getLong("next_before");
                ui(() -> {
                    activeBase = base; activeToken = secret;
                    getSharedPreferences("receiver", MODE_PRIVATE).edit().putString("address", base).putString("token", secret).apply();
                    displayed += files.length(); pageCursor = cursor;
                    status.setText(displayed == 0 ? "接收端暂无已完成的照片" : "已列出 " + displayed + " 个原始文件");
                    for (int i = 0; i < files.length(); i++) {
                        try { addFile(files.getJSONObject(i)); } catch (JSONException error) { status.setText("列表格式无效"); }
                    }
                    connect.setEnabled(true);
                    nextPage.setEnabled(pageCursor != null);
                });
            } catch (Exception error) {
                ui(() -> { status.setText("连接失败：" + error.getMessage()); connect.setEnabled(true); nextPage.setEnabled(pageCursor != null); });
            } finally { if (request != null) request.disconnect(); }
        });
    }

    private void addFile(JSONObject file) throws JSONException {
        String name = file.getString("filename");
        LinearLayout row = new LinearLayout(this); row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(0, dp(12), 0, dp(12));
        TextView label = new TextView(this); label.setText(name + "  ·  " + file.getString("day") + "\n" +
                String.format(java.util.Locale.ROOT, "%.1f MB", file.getLong("size") / 1000000.0));
        label.setTextSize(16); row.addView(label);
        LinearLayout buttons = new LinearLayout(this);
        Button save = new Button(this); save.setText("保存原图");
        Button share = new Button(this); share.setText("保存并分享");
        buttons.addView(save); buttons.addView(share); row.addView(buttons); list.addView(row);
        save.setOnClickListener(v -> download(file, false, save, share));
        share.setOnClickListener(v -> download(file, true, save, share));
    }

    private void download(JSONObject file, boolean share, Button first, Button second) {
        first.setEnabled(false); second.setEnabled(false); connect.setEnabled(false); nextPage.setEnabled(false);
        final String base = activeBase, secret = activeToken;
        status.setText("正在保存并校验原始文件…");
        worker.execute(() -> {
            Uri pending = null;
            HttpURLConnection request = null;
            try {
                String id = file.getString("id"), name = file.getString("filename");
                long expectedSize = file.getLong("size");
                if (!id.matches("[0-9a-f]{64}") || name.contains("/") || name.contains("\\") ||
                        expectedSize < 0 || expectedSize > 16L * 1024 * 1024 * 1024) throw new IOException("无效文件信息");
                String mime = file.optString("mime", "application/octet-stream");
                ContentValues values = new ContentValues();
                values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
                values.put(MediaStore.MediaColumns.MIME_TYPE, mime);
                values.put(MediaStore.MediaColumns.IS_PENDING, 1);
                boolean image = name.toLowerCase(java.util.Locale.ROOT).matches(".*\\.(jpg|jpeg)$");
                boolean video = name.toLowerCase(java.util.Locale.ROOT).endsWith(".mp4");
                Uri collection = image ? MediaStore.Images.Media.EXTERNAL_CONTENT_URI : video ?
                        MediaStore.Video.Media.EXTERNAL_CONTENT_URI : MediaStore.Downloads.EXTERNAL_CONTENT_URI;
                values.put(MediaStore.MediaColumns.RELATIVE_PATH, image ? "Pictures/NXmini" : video ? "Movies/NXmini" : "Download/NXmini");
                pending = getContentResolver().insert(collection, values);
                if (pending == null) throw new IOException("无法创建手机文件");
                request = connection(base, "/api/files/" + id, secret);
                MessageDigest digest = MessageDigest.getInstance("SHA-256"); long total = 0;
                try (InputStream source = request.getInputStream(); OutputStream output = getContentResolver().openOutputStream(pending)) {
                    if (output == null) throw new IOException("无法写入手机存储");
                    byte[] buffer = new byte[65536]; int count;
                    while ((count = source.read(buffer)) != -1) {
                        if (Thread.currentThread().isInterrupted()) throw new IOException("下载已停止");
                        total += count;
                        if (total > expectedSize) throw new IOException("文件长度不符");
                        digest.update(buffer, 0, count); output.write(buffer, 0, count);
                    }
                }
                StringBuilder actual = new StringBuilder();
                for (byte value : digest.digest()) actual.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
                if (total != expectedSize || !id.equals(actual.toString())) throw new IOException("文件校验失败，请重新下载");
                ContentValues completed = new ContentValues(); completed.put(MediaStore.MediaColumns.IS_PENDING, 0);
                getContentResolver().update(pending, completed, null, null);
                final Uri saved = pending; pending = null;
                ui(() -> {
                    status.setText("原始文件已保存");
                    if (share) {
                        Intent send = new Intent(Intent.ACTION_SEND).setType(mime).putExtra(Intent.EXTRA_STREAM, saved);
                        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                        send.setClipData(ClipData.newUri(getContentResolver(), name, saved));
                        startActivity(Intent.createChooser(send, "分享原图，可选择 Quick Share"));
                    }
                });
            } catch (Exception error) {
                ui(() -> status.setText("保存失败：" + error.getMessage()));
            } finally {
                if (request != null) request.disconnect();
                if (pending != null) getContentResolver().delete(pending, null, null);
                ui(() -> { first.setEnabled(true); second.setEnabled(true); connect.setEnabled(true); nextPage.setEnabled(pageCursor != null); });
            }
        });
    }

    @Override public void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }
}
