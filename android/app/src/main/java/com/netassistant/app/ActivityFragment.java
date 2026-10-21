package com.netassistant.app;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

public class ActivityFragment extends Fragment {
    private RecyclerView rv;
    private AuditAdapter adapter;
    private View rootView;
    private List<AuditRecord> items = new ArrayList<>();

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_activity, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View root, @Nullable Bundle savedInstanceState) {
        rootView = root;
        rv = root.findViewById(R.id.audit);
        rv.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new AuditAdapter(requireContext(), items);
        rv.setAdapter(adapter);
        root.findViewById(R.id.audit_refresh).setOnClickListener(v -> load());
        load();
    }

    @Override
    public void onResume() {
        super.onResume();
        load();
    }

    private void load() {
        Api.get(requireContext(), "/audit?limit=100", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                items.clear();
                JSONArray arr = body.optJSONArray("records");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        try {
                            items.add(new AuditRecord(arr.getJSONObject(i)));
                        } catch (JSONException ignored) {
                        }
                    }
                }
                adapter.notifyDataSetChanged();
                rootView.findViewById(R.id.audit_empty).setVisibility(items.isEmpty() ? View.VISIBLE : View.GONE);
            }

            @Override
            public void fail(String message) {
                Ui.toast(requireContext(), "Журнал: " + message);
            }
        });
    }

    public static final class AuditRecord {
        public final String event;
        public final String device;
        public final String action;
        public final String result;
        public final String text;
        public final String iso;

        AuditRecord(JSONObject o) {
            event = o.optString("event", "");
            device = o.optString("device", "");
            action = o.optString("action", "");
            result = o.optString("result", "");
            text = o.optString("text", "");
            iso = o.optString("iso", "");
        }
    }

    public static final class AuditAdapter extends RecyclerView.Adapter<AuditAdapter.Holder> {
        private final android.content.Context c;
        private final List<AuditRecord> items;

        AuditAdapter(android.content.Context c, List<AuditRecord> items) {
            this.c = c;
            this.items = items;
        }

        @NonNull
        @Override
        public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            LinearLayout row = new LinearLayout(c);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(android.view.Gravity.CENTER_VERTICAL);
            row.setPadding(Ui.dp(c, 12), Ui.dp(c, 10), Ui.dp(c, 12), Ui.dp(c, 10));
            return new Holder(row);
        }

        @Override
        public void onBindViewHolder(@NonNull Holder h, int pos) {
            h.bind(items.get(pos));
        }

        @Override
        public int getItemCount() {
            return items.size();
        }

        class Holder extends RecyclerView.ViewHolder {
            final android.view.View dot;
            final TextView title;
            final TextView subtitle;
            final TextView time;

            Holder(LinearLayout row) {
                super(row);
                this.dot = new android.view.View(c);
                LinearLayout.LayoutParams dp = new LinearLayout.LayoutParams(Ui.dp(c, 12), Ui.dp(c, 12));
                dp.setMargins(0, 0, Ui.dp(c, 12), 0);
                row.addView(dot, dp);
                LinearLayout texts = new LinearLayout(c);
                texts.setOrientation(LinearLayout.VERTICAL);
                this.title = Ui.text(c, "", Ui.color(c, R.color.text), 15f, true, android.view.Gravity.START);
                this.subtitle = Ui.text(c, "", Ui.color(c, R.color.muted), 13f, false, android.view.Gravity.START);
                texts.addView(title);
                texts.addView(subtitle);
                row.addView(texts, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                this.time = Ui.text(c, "", Ui.color(c, R.color.muted), 12f, false, android.view.Gravity.END);
                row.addView(time);
            }

            void bind(AuditRecord r) {
                dot.setBackground(Ui.solid(dotColor(r.event), 6, c));
                title.setText(eventLabel(r.event));
                List<String> parts = new java.util.ArrayList<>();
                if (r.device != null && !r.device.isEmpty()) parts.add(r.device);
                if (r.action != null && !r.action.isEmpty()) parts.add(r.action);
                if (r.result != null && !r.result.isEmpty()) parts.add("→ " + r.result);
                subtitle.setText(android.text.TextUtils.join(" · ", parts));
                time.setText(r.iso.isEmpty() ? "" : r.iso.substring(11, 16));
            }
        }

        private int dotColor(String event) {
            switch (event) {
                case "error": return Ui.color(c, R.color.danger);
                case "pending": return Ui.color(c, R.color.warning);
                case "result": return Ui.color(c, R.color.success);
                case "command": return Ui.color(c, R.color.primary);
                default: return Ui.color(c, R.color.muted);
            }
        }

        private String eventLabel(String event) {
            switch (event) {
                case "command": return "Команда";
                case "result": return "Результат";
                case "error": return "Ошибка";
                case "pending": return "Требуется подтверждение";
                case "model": return "Модель ИИ";
                case "chat": return "Сообщение";
                case "session": return "Сессия";
                case "agent": return "Устройство";
                case "file": return "Файл";
                case "voice": return "Голос";
                default: return event.isEmpty() ? "Запись" : event.substring(0, 1).toUpperCase(Locale.getDefault()) + event.substring(1);
            }
        }
    }
}
