package com.netassistant.app;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.graphics.Typeface;
import android.util.TypedValue;
import android.content.ContentResolver;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Bundle;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

import okhttp3.MediaType;
import okhttp3.Request;
import okhttp3.RequestBody;

public class ChatFragment extends Fragment {
    private static final String STATE_AT_BOTTOM = "at_bottom";

    private RecyclerView messagesView;
    private EditText input;
    private ImageButton sendButton, attachButton, micButton;
    private TextView status;

    private final List<Message> items = new ArrayList<>();
    private MessageAdapter adapter;
    private Handler poller = new Handler(Looper.getMainLooper());
    private boolean polling = true;
    private boolean atBottom = true;
    private boolean sending = false;
    private boolean recording = false;
    private MediaRecorder recorder;
    private File voiceFile;
    private MediaPlayer ttsPlayer;
    private String lastTtsText = "";

    private String session = "";

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container, @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_chat, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        if (savedInstanceState != null) {
            atBottom = savedInstanceState.getBoolean(STATE_AT_BOTTOM, true);
        }

        messagesView = view.findViewById(R.id.messages);
        input = view.findViewById(R.id.input);
        sendButton = view.findViewById(R.id.send_button);
        attachButton = view.findViewById(R.id.attach_button);
        micButton = view.findViewById(R.id.mic_button);
        status = view.findViewById(R.id.chat_status);

        messagesView.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new MessageAdapter(requireContext(), items, this::onConfirm);
        messagesView.setAdapter(adapter);
        messagesView.addOnScrollListener(new RecyclerView.OnScrollListener() {
            @Override
            public void onScrolled(@NonNull RecyclerView rv, int dx, int dy) {
                LinearLayoutManager lm = (LinearLayoutManager) rv.getLayoutManager();
                if (lm != null) {
                    int last = lm.findLastCompletelyVisibleItemPosition();
                    atBottom = last >= items.size() - 2;
                }
            }
        });

        sendButton.setOnClickListener(v -> sendMessage());
        attachButton.setOnClickListener(v -> pickFile());
        micButton.setOnClickListener(v -> toggleVoice());

        loadSession();
    }

    @Override
    public void onSaveInstanceState(@NonNull Bundle outState) {
        super.onSaveInstanceState(outState);
        outState.putBoolean(STATE_AT_BOTTOM, atBottom);
    }

    @Override
    public void onResume() {
        super.onResume();
        startPolling();
    }

    @Override
    public void onPause() {
        super.onPause();
        stopPolling();
        if (recording) {
            stopRecording();
        }
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        stopPolling();
        if (recorder != null) {
            recorder.release();
            recorder = null;
        }
        if (ttsPlayer != null) {
            ttsPlayer.release();
            ttsPlayer = null;
        }
        poller.removeCallbacksAndMessages(null);
    }

    /* ─────────────────── сессия и история ─────────────────── */

    private void loadSession() {
        if (!isAdded()) return;
        Api.get(requireContext(), "/session", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                if (!isAdded()) return;
                session = body.optString("session", "");
                new Prefs(requireContext()).setSession(session);
                loadHistory();
            }

            @Override
            public void fail(String message) {
                if (!isAdded()) return;
                session = new Prefs(requireContext()).session();
                loadHistory();
                Ui.toast(requireContext(), "Сессия: " + (message.isEmpty() ? "ошибка" : message));
            }
        });
    }

    private void loadHistory() {
        if (!isAdded()) return;
        Api.get(requireContext(), "/history?session=" + session + "&limit=200", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                if (!isAdded()) return;
                items.clear();
                JSONArray arr = body.optJSONArray("messages");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        try {
                            items.add(new Message(arr.getJSONObject(i)));
                        } catch (JSONException ignored) {
                        }
                    }
                }
                adapter.notifyDataSetChanged();
                if (atBottom && items.size() > 0) {
                    messagesView.scrollToPosition(items.size() - 1);
                }
                status.setText("История синхронизирована (" + items.size() + " сообщений)");
            }

            @Override
            public void fail(String message) {
                if (!isAdded()) return;
                Ui.toast(requireContext(), "Не удалось загрузить историю: " + message);
            }
        });
    }

    private void startPolling() {
        polling = true;
        poll();
    }

    private void stopPolling() {
        polling = false;
    }

    private void poll() {
        if (!polling || sending || recording) {
            poller.postDelayed(this::poll, 4000);
            return;
        }
        loadHistory();
        poller.postDelayed(this::poll, 4000);
    }

    /* ─────────────────── отправка сообщения ─────────────────── */

    private void sendMessage() {
        String text = input.getText() != null ? input.getText().toString().trim() : "";
        if (text.isEmpty() || sending) {
            return;
        }
        sending = true;
        input.setEnabled(false);
        sendButton.setEnabled(false);
        items.add(new Message("user", text, "", System.currentTimeMillis(), null, null, null));
        adapter.notifyItemInserted(items.size() - 1);
        messagesView.scrollToPosition(items.size() - 1);
        input.setText("");

        JSONObject body = new JSONObject();
        try {
            body.put("message", text);
            body.put("session", session);
        } catch (JSONException ignored) {
        }
        Api.post(requireContext(), "/chat?source=android", body, new Api.Cb() {
            @Override
            public void ok(JSONObject turn) {
                if (!isAdded()) return;
                sending = false;
                input.setEnabled(true);
                sendButton.setEnabled(true);
                loadHistory();
                maybeSpeak(turn.optString("reply", ""));
            }

            @Override
            public void fail(String message) {
                if (!isAdded()) return;
                sending = false;
                input.setEnabled(true);
                sendButton.setEnabled(true);
                if (!items.isEmpty() && items.get(items.size() - 1).role.equals("user") && items.get(items.size() - 1).text.equals(text)) {
                    items.remove(items.size() - 1);
                    adapter.notifyItemRemoved(items.size());
                }
                Ui.toast(requireContext(), "Не удалось отправить: " + message);
            }
        });
    }

    /* ─────────────────── вложения ─────────────────── */

    private final ActivityResultLauncher<String> pickFileLauncher = registerForActivityResult(
            new ActivityResultContracts.GetContent(),
            uri -> {
                if (uri == null) {
                    return;
                }
                String msg = input.getText() != null ? input.getText().toString().trim() : "";
                uploadFile(uri, msg);
            });

    private void pickFile() {
        pickFileLauncher.launch("*/*");
    }

    private void uploadFile(Uri uri, String message) {
        ContentResolver cr = requireContext().getContentResolver();
        String mime = cr.getType(uri);
        String name = uri.getLastPathSegment();
        if (name == null || name.isEmpty()) {
            name = "file";
        }
        boolean isImage = mime != null && mime.startsWith("image/");
        String path = isImage ? "/image?chat=true&session=" + session + "&source=android"
                : "/file?chat=true&session=" + session + "&source=android";
        if (message != null && !message.isEmpty()) {
            path += "&message=" + android.net.Uri.encode(message);
        }

        Api.postMultipart(requireContext(), path, Api.multipart()
                .addFormDataPart("file", name, Api.streamBody(cr, uri, mime))
                .addFormDataPart("session", session)
                .addFormDataPart("source", "android")
                .addFormDataPart("chat", "true")
                .build(), new Api.Cb() {
            @Override
            public void ok(JSONObject turn) {
                if (!isAdded()) return;
                Ui.toast(requireContext(), "Файл сохранён на SSD роутера и отправлен ИИ");
                input.setText("");
                loadHistory();
                maybeSpeak(turn.optString("reply", ""));
            }

            @Override
            public void fail(String message) {
                if (!isAdded()) return;
                Ui.toast(requireContext(), "Не удалось отправить файл: " + message);
            }
        });
    }

    /* ─────────────────── голосовой ввод ─────────────────── */

    private void toggleVoice() {
        if (!isAdded()) return;
        if (recording) {
            stopRecording();
            return;
        }
        if (ContextCompat.checkSelfPermission(requireContext(), Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            Ui.toast(requireContext(), "Запись голоса требует разрешения на микрофон (RECORD_AUDIO)");
            return;
        }
        startRecording();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (!isAdded()) return;
        if (requestCode == 1001 && grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startRecording();
        } else {
            Ui.toast(requireContext(), "Нужен доступ к микрофону для голосовых сообщений");
        }
    }

    private void startRecording() {
        recording = true;
        micButton.setImageResource(R.drawable.ic_stop);
        micButton.setColorFilter(ContextCompat.getColor(requireContext(), R.color.danger));
        voiceFile = new File(requireContext().getCacheDir(), "voice.m4a");
        recorder = new MediaRecorder();
        recorder.setAudioSource(MediaRecorder.AudioSource.MIC);
        recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
        recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
        recorder.setOutputFile(voiceFile.getAbsolutePath());
        try {
            recorder.prepare();
            recorder.start();
        } catch (Exception e) {
            recording = false;
            micButton.setImageResource(R.drawable.ic_mic);
            micButton.setColorFilter(null);
            Ui.toast(requireContext(), "Не удалось начать запись: " + e.getMessage());
            return;
        }
        Ui.toast(requireContext(), "Запись голоса… нажмите ещё раз, чтобы отправить");
    }

    private void stopRecording() {
        if (!recording || recorder == null) {
            return;
        }
        recording = false;
        micButton.setImageResource(R.drawable.ic_mic);
        micButton.setColorFilter(null);
        try {
            recorder.stop();
            recorder.reset();
            recorder.release();
        } catch (Exception ignored) {
        }
        recorder = null;

        if (voiceFile != null && voiceFile.exists() && voiceFile.length() > 1000) {
            ContentResolver cr = requireContext().getContentResolver();
            Api.postMultipart(requireContext(), "/voice?chat=true&session=" + session + "&source=android",
                    Api.multipart()
                            .addFormDataPart("file", "voice.m4a", Api.streamBody(cr, Uri.fromFile(voiceFile), "audio/mp4"))
                            .addFormDataPart("session", session)
                            .addFormDataPart("source", "android")
                            .addFormDataPart("chat", "true")
                            .build(), new Api.Cb() {
                        @Override
                        public void ok(JSONObject turn) {
                            if (!isAdded()) return;
                            String heard = turn.optString("text", "");
                            Ui.toast(requireContext(), heard.isEmpty() ? "Голосовое отправлено" : "Распознано: " + heard);
                            loadHistory();
                            maybeSpeak(turn.optString("reply", ""));
                        }

                        @Override
                        public void fail(String message) {
                            if (!isAdded()) return;
                            Ui.toast(requireContext(), "Голос не распознан: " + message);
                        }
                    });
        } else {
            Ui.toast(requireContext(), "Запись слишком короткая");
        }
    }

    /* ─────────────────── озвучка ответа ─────────────────── */

    private void maybeSpeak(String text) {
        if (!isAdded() || text == null || text.isEmpty() || text.equals(lastTtsText) || !new Prefs(requireContext()).tts()) {
            return;
        }
        lastTtsText = text;
        try {
            JSONObject body = new JSONObject().put("text", text);
            Request req = new Request.Builder()
                    .url(Api.url(requireContext(), "/tts"))
                    .header("Authorization", "Bearer " + new Prefs(requireContext()).token())
                    .post(RequestBody.create(body.toString(), MediaType.get("application/json; charset=utf-8")))
                    .build();
            Api.client().newCall(req).enqueue(new okhttp3.Callback() {
                @Override
                public void onFailure(okhttp3.Call call, java.io.IOException e) {
                    // игнорируем ошибки озвучки
                }

                @Override
                public void onResponse(okhttp3.Call call, okhttp3.Response response) {
                    try {
                        if (!response.isSuccessful() || response.body() == null) {
                            return;
                        }
                        File out = new File(requireContext().getCacheDir(), "reply.ogg");
                        java.io.InputStream in = response.body().byteStream();
                        java.io.FileOutputStream fos = new java.io.FileOutputStream(out);
                        byte[] buf = new byte[8192];
                        int n;
                        while ((n = in.read(buf)) > 0) {
                            fos.write(buf, 0, n);
                        }
                        fos.close();
                        in.close();
                        new Handler(Looper.getMainLooper()).post(() -> playOgg(out));
                    } catch (Exception ignored) {
                    } finally {
                        response.close();
                    }
                }
            });
        } catch (JSONException ignored) {
        }
    }

    private void playOgg(File file) {
        if (ttsPlayer != null) {
            ttsPlayer.release();
        }
        ttsPlayer = new MediaPlayer();
        try {
            ttsPlayer.setDataSource(file.getAbsolutePath());
            ttsPlayer.prepare();
            ttsPlayer.start();
        } catch (Exception e) {
            if (ttsPlayer != null) {
                ttsPlayer.release();
                ttsPlayer = null;
            }
        }
    }

    /* ─────────────────── подтверждение опасных команд ─────────────────── */

    private void onConfirm(Message message, JSONObject pending) {
        if (!isAdded()) return;
        String token = pending.optString("token", "");
        String device = pending.optString("device", "");
        String action = pending.optString("action", "");
        String question = pending.optString("question", "Подтвердите выполнение команды");

        new androidx.appcompat.app.AlertDialog.Builder(requireContext())
                .setTitle("Подтверждение")
                .setMessage(question + "\n\n" + device + " → " + action)
                .setPositiveButton("Подтвердить", (d, w) -> {
                    if (!isAdded()) return;
                    String confirmPath = "/confirm/" + token + "?session=" + session + "&source=android";
                    Api.post(requireContext(), confirmPath, new JSONObject(), new Api.Cb() {
                        @Override
                        public void ok(JSONObject body) {
                            if (!isAdded()) return;
                            Ui.toast(requireContext(), "Готово: " + body.optString("action", action));
                            loadHistory();
                        }

                        @Override
                        public void fail(String message) {
                            if (!isAdded()) return;
                            Ui.toast(requireContext(), "Не удалось подтвердить: " + message);
                        }
                    });
                })
                .setNegativeButton("Отмена", null)
                .show();
    }

    /* ─────────────────── модель сообщения ─────────────────── */

    public static final class Message {
        public final String role;
        public final String text;
        public final String source;
        public final long ts;
        public final JSONArray executed;
        public final JSONArray pending;
        public final JSONObject file;

        public Message(JSONObject o) {
            role = o.optString("role", "");
            text = o.optString("text", "");
            source = o.optString("source", "");
            ts = o.optLong("ts", 0);
            executed = o.optJSONArray("executed");
            pending = o.optJSONArray("pending");
            file = o.optJSONObject("file");
        }

        public Message(String role, String text, String source, long ts,
                       JSONArray executed, JSONArray pending, JSONObject file) {
            this.role = role;
            this.text = text;
            this.source = source;
            this.ts = ts;
            this.executed = executed;
            this.pending = pending;
            this.file = file;
        }
    }

    /* ─────────────────── адаптер списка ─────────────────── */

    public static final class MessageAdapter extends RecyclerView.Adapter<MessageAdapter.Holder> {
        private final Context c;
        private final List<Message> items;
        private final ConfirmListener listener;

        public interface ConfirmListener {
            void onConfirm(Message message, JSONObject pending);
        }

        public MessageAdapter(Context c, List<Message> items, ConfirmListener listener) {
            this.c = c;
            this.items = items;
            this.listener = listener;
        }

        @NonNull
        @Override
        public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            LinearLayout row = new LinearLayout(c);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(4, 4, 4, 4);
            return new Holder(row);
        }

        @Override
        public void onBindViewHolder(@NonNull Holder h, int pos) {
            Message m = items.get(pos);
            h.bind(m);
        }

        @Override
        public int getItemCount() {
            return items.size();
        }

        class Holder extends RecyclerView.ViewHolder {
            final LinearLayout row;
            final LinearLayout bubbles; // vertical stack inside row cell
            final TextView time;

            Holder(LinearLayout row) {
                super(row);
                this.row = row;
                this.bubbles = new LinearLayout(c);
                bubbles.setOrientation(LinearLayout.VERTICAL);
                this.time = new TextView(c);
                time.setTextSize(11f);
                time.setTextColor(Ui.color(c, R.color.muted));
                time.setTypeface(Typeface.DEFAULT_BOLD);
                row.addView(bubbles, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                row.addView(time, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            }

            void bind(Message m) {
                bubbles.removeAllViews();
                boolean user = m.role.equals("user");
                boolean system = m.role.equals("system");

                int bg;
                int txt;
                int gravity;
                if (user) {
                    bg = R.drawable.bg_bubble_user;
                    txt = 0xFFFFFFFF;
                    gravity = Gravity.END;
                } else if (system) {
                    bg = R.drawable.bg_bubble_system;
                    txt = Ui.color(c, R.color.muted);
                    gravity = Gravity.CENTER;
                } else {
                    bg = R.drawable.bg_bubble_ai;
                    txt = Ui.color(c, R.color.text);
                    gravity = Gravity.START;
                }

                TextView bubble = Ui.text(c, m.text, txt, 15f, false, gravity);
                bubble.setBackground(ContextCompat.getDrawable(c, bg));
                int pad = Ui.dp(c, 12);
                bubble.setPadding(pad, Ui.dp(c, 10), pad, Ui.dp(c, 10));
                bubble.setLineSpacing(2f, 1.05f);
                bubbles.addView(bubble, new LinearLayout.LayoutParams(
                        user ? ViewGroup.LayoutParams.WRAP_CONTENT : ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT, user ? 0.8f : 1f));
                ((LinearLayout.LayoutParams) bubble.getLayoutParams()).gravity = gravity;

                // executed actions
                if (m.executed != null) {
                    for (int i = 0; i < m.executed.length(); i++) {
                        try {
                            JSONObject e = m.executed.getJSONObject(i);
                            TextView chip = Ui.text(c, "✅ " + e.optString("device", "") + " → " + e.optString("action", ""),
                                    Ui.color(c, R.color.secondary), 12f, true, gravity);
                            chip.setBackground(Ui.solid(Ui.color(c, R.color.chip), 10, c).mutate());
                            chip.setPadding(Ui.dp(c, 10), Ui.dp(c, 6), Ui.dp(c, 10), Ui.dp(c, 6));
                            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                            lp.topMargin = Ui.dp(c, 6);
                            lp.gravity = gravity;
                            bubbles.addView(chip, lp);
                        } catch (JSONException ignored) {
                        }
                    }
                }

                // pending confirmations
                if (m.pending != null) {
                    for (int i = 0; i < m.pending.length(); i++) {
                        try {
                            JSONObject p = m.pending.getJSONObject(i);
                            TextView btn = Ui.text(c, "⚠ Подтвердить: " + p.optString("device", "") + " → " + p.optString("action", ""),
                                    Ui.color(c, R.color.warning), 13f, true, gravity);
                            btn.setBackground(Ui.solid(0xFFFDECEA, 12, c));
                            btn.setPadding(Ui.dp(c, 12), Ui.dp(c, 8), Ui.dp(c, 12), Ui.dp(c, 8));
                            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                            lp.topMargin = Ui.dp(c, 8);
                            lp.gravity = gravity;
                            btn.setOnClickListener(v -> listener.onConfirm(m, p));
                            bubbles.addView(btn, lp);
                        } catch (JSONException ignored) {
                        }
                    }
                }

                // source label
                if (m.source != null && !m.source.isEmpty() && !m.source.equals("android")) {
                    TextView src = Ui.text(c, "🌐 из другого устройства (" + m.source + ")",
                            Ui.color(c, R.color.primary_dark), 11f, true, gravity);
                    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                    lp.topMargin = Ui.dp(c, 6);
                    lp.gravity = gravity;
                    bubbles.addView(src, lp);
                }

                time.setText(fmtTime(m.ts));
            }
        }

        private String fmtTime(long ts) {
            if (ts <= 0) return "";
            java.text.SimpleDateFormat sdf = new java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault());
            return sdf.format(new java.util.Date(ts));
        }
    }
}
