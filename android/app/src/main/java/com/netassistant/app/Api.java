package com.netassistant.app;

import android.content.ContentResolver;
import android.content.Context;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.TimeUnit;

import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.MediaType;
import okhttp3.MultipartBody;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

public final class Api {
    public interface Cb {
        void ok(JSONObject body);

        void fail(String message);
    }

    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private static final OkHttpClient CLIENT = new OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(240, TimeUnit.SECONDS)
            .writeTimeout(240, TimeUnit.SECONDS)
            .build();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private Api() {
    }

    public static OkHttpClient client() {
        return CLIENT;
    }

    public static String base(Context c) {
        String s = new Prefs(c).server().trim();
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        return s;
    }

    public static String url(Context c, String path) {
        return base(c) + path;
    }

    private static Request.Builder builder(Context c, String path) {
        Request.Builder b = new Request.Builder().url(url(c, path));
        String token = new Prefs(c).token();
        if (!token.isEmpty()) {
            b.header("Authorization", "Bearer " + token);
        }
        return b;
    }

    public static void get(Context c, String path, Cb cb) {
        enqueue(builder(c, path).get().build(), cb);
    }

    public static void delete(Context c, String path, Cb cb) {
        enqueue(builder(c, path).delete().build(), cb);
    }

    public static void post(Context c, String path, JSONObject json, Cb cb) {
        enqueue(builder(c, path).post(RequestBody.create(json.toString(), JSON)).build(), cb);
    }

    public static MultipartBody.Builder multipart() {
        return new MultipartBody.Builder().setType(MultipartBody.FORM);
    }

    public static void postMultipart(Context c, String path, MultipartBody body, Cb cb) {
        enqueue(builder(c, path).post(body).build(), cb);
    }

    public static RequestBody streamBody(final ContentResolver cr, final Uri uri, final String mime) {
        final MediaType type = MediaType.get(mime == null ? "application/octet-stream" : mime);
        return new RequestBody() {
            @Override
            public MediaType contentType() {
                return type;
            }

            @Override
            public void writeTo(okio.BufferedSink sink) throws IOException {
                InputStream in = cr.openInputStream(uri);
                if (in == null) {
                    throw new IOException("не удалось открыть файл");
                }
                try {
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        sink.write(buf, 0, n);
                    }
                } finally {
                    in.close();
                }
            }
        };
    }

    public static void enqueue(Request req, Cb cb) {
        CLIENT.newCall(req).enqueue(new Callback() {
            @Override
            public void onFailure(Call call, IOException e) {
                deliverFail(cb, "нет связи: " + e.getMessage());
            }

            @Override
            public void onResponse(Call call, Response response) {
                String text = "";
                try {
                    if (response.body() != null) {
                        text = response.body().string();
                    }
                } catch (IOException e) {
                    deliverFail(cb, e.getMessage());
                    return;
                }
                if (!response.isSuccessful()) {
                    deliverFail(cb, errorText(response.code(), text));
                    return;
                }
                if (text.isEmpty()) {
                    text = "{}";
                }
                try {
                    JSONObject obj = new JSONObject(text);
                    MAIN.post(() -> cb.ok(obj));
                } catch (JSONException e) {
                    MAIN.post(() -> cb.fail("некорректный ответ сервера"));
                }
            }
        });
    }

    private static String errorText(int code, String text) {
        try {
            JSONObject o = new JSONObject(text);
            String detail = o.optString("detail", "");
            if (!detail.isEmpty()) {
                return detail;
            }
        } catch (JSONException ignored) {
        }
        return "HTTP " + code + (text.isEmpty() ? "" : ": " + text.substring(0, Math.min(160, text.length())));
    }

    private static void deliverFail(final Cb cb, final String msg) {
        MAIN.post(() -> cb.fail(msg));
    }
}
