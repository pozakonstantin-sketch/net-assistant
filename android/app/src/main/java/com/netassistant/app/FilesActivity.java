package com.netassistant.app;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

import androidx.appcompat.app.AppCompatActivity;

public class FilesActivity extends AppCompatActivity {
    private RecyclerView list;
    private FileAdapter adapter;
    private List<FileItem> items = new ArrayList<>();

    @Override
    protected void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_files);

        list = findViewById(R.id.files_list);
        list.setLayoutManager(new LinearLayoutManager(this));
        adapter = new FileAdapter(this, items);
        list.setAdapter(adapter);

        SwipeRefreshLayout sw = findViewById(R.id.files_refresh);
        sw.setOnRefreshListener(() -> {
            load();
            sw.setRefreshing(false);
        });
        load();
    }

    private void load() {
        Api.get(this, "/files?limit=100", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                items.clear();
                JSONArray arr = body.optJSONArray("files");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        try {
                            items.add(new FileItem(arr.getJSONObject(i)));
                        } catch (JSONException ignored) {
                        }
                    }
                }
                adapter.notifyDataSetChanged();
                findViewById(R.id.files_empty).setVisibility(items.isEmpty() ? View.VISIBLE : View.GONE);
            }

            @Override
            public void fail(String message) {
                Ui.toast(FilesActivity.this, "Файлы: " + message);
            }
        });
    }

    public static final class FileItem {
        public final String id, name, kind, size, chars, uploaded, routerPath, preview;

        FileItem(JSONObject o) {
            id = o.optString("id", "");
            name = o.optString("name", "");
            kind = o.optString("kind", "");
            size = o.optString("size", "");
            uploaded = o.optString("uploaded", "");
            routerPath = o.optString("router_path", "");
            preview = o.optString("preview", "");
            Object ch = o.opt("chars");
            chars = ch != null ? String.valueOf(ch) : "";
        }
    }

    public static final class FileAdapter extends RecyclerView.Adapter<FileAdapter.Holder> {
        private final AppCompatActivity c;
        private final List<FileItem> items;

        FileAdapter(AppCompatActivity c, List<FileItem> items) {
            this.c = c;
            this.items = items;
        }

        @NonNull
        @Override
        public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            LinearLayout row = new LinearLayout(c);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(android.view.Gravity.CENTER_VERTICAL);
            row.setPadding(Ui.dp(c, 14), Ui.dp(c, 12), Ui.dp(c, 14), Ui.dp(c, 12));
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
            final TextView icon;
            final TextView name;
            final TextView meta;
            final TextView path;

            Holder(LinearLayout row) {
                super(row);
                this.icon = Ui.text(c, "", 0xFF6C4DFF, 30f, false, android.view.Gravity.START);
                LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(Ui.dp(c, 52), Ui.dp(c, 52));
                ip.setMargins(0, 0, Ui.dp(c, 14), 0);
                row.addView(icon, ip);
                LinearLayout texts = new LinearLayout(c);
                texts.setOrientation(LinearLayout.VERTICAL);
                this.name = Ui.text(c, "", Ui.color(c, R.color.text), 15f, true, android.view.Gravity.START);
                this.meta = Ui.text(c, "", Ui.color(c, R.color.muted), 13f, false, android.view.Gravity.START);
                this.path = Ui.text(c, "", Ui.color(c, R.color.primary), 11f, false, android.view.Gravity.START);
                path.setMaxLines(1);
                path.setEllipsize(android.text.TextUtils.TruncateAt.MIDDLE);
                texts.addView(name);
                texts.addView(meta);
                texts.addView(path);
                row.addView(texts, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            }

            void bind(FileItem f) {
                icon.setText(kindIcon(f.kind));
                name.setText(f.name.isEmpty() ? "файл" : f.name);
                String meta = (f.kind.isEmpty() ? "" : f.kind + "  ") + (f.size.isEmpty() ? "" : f.size + "  ") + f.uploaded;
                this.meta.setText(meta);
                this.path.setText(f.routerPath);
                itemView.setOnClickListener(v -> showPreview(f));
            }

            private String kindIcon(String kind) {
                switch (kind) {
                    case "document": return "📄";
                    case "spreadsheet": return "📊";
                    case "pdf": return "📕";
                    case "text": return "📝";
                    case "archive": return "📦";
                    case "image": return "🖼️";
                    case "audio": return "🎵";
                    case "video": return "🎬";
                    default: return "📎";
                }
            }

            private void showPreview(FileItem f) {
                TextView tv = new TextView(c);
                tv.setText((f.preview.isEmpty() ? "Нет извлечённого текста" : f.preview));
                tv.setTextColor(Ui.color(c, R.color.text));
                tv.setTextSize(14f);
                tv.setPadding(Ui.dp(c, 16), Ui.dp(c, 16), Ui.dp(c, 16), Ui.dp(c, 16));
                tv.setLineSpacing(2f, 1.05f);
                new androidx.appcompat.app.AlertDialog.Builder(c)
                        .setTitle(f.name)
                        .setMessage("Путь на роутере: " + f.routerPath + "\n\nИзвлечённый текст:\n" + tv.getText())
                        .setPositiveButton("Закрыть", null)
                        .show();
            }
        }
    }
}
