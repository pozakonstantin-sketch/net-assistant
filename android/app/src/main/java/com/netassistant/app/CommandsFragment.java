package com.netassistant.app;

import android.app.AlertDialog;
import android.app.Dialog;
import android.content.DialogInterface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.DialogFragment;
import androidx.fragment.app.Fragment;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public class CommandsFragment extends Fragment {
    private LinearLayout container;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_commands, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        container = view.findViewById(R.id.commands_container);
        build();
    }

    private void build() {
        container.removeAllViews();
        List<Commands.Cmd> all = Commands.all();

        // Обложка раздела
        ImageViewEx hero = new ImageViewEx(requireContext());
        hero.setImageResource(R.drawable.hero_commands);
        hero.setScaleType(ImageViewEx.ScaleType.CENTER_CROP);
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, Ui.dp(requireContext(), 170));
        container.addView(hero, hp);

        TextView title = Ui.text(requireContext(), getString(R.string.commands_title), 0xFF1B1633, 26f, true, Gravity.START);
        title.setPadding(0, Ui.dp(requireContext(), 14), 0, Ui.dp(requireContext(), 4));
        container.addView(title);
        TextView sub = Ui.text(requireContext(), getString(R.string.commands_subtitle), Ui.color(requireContext(), R.color.muted), 14f, false, Gravity.START);
        sub.setPadding(0, 0, 0, Ui.dp(requireContext(), 16));
        container.addView(sub);

        for (String section : Commands.sections()) {
            List<Commands.Cmd> items = Commands.bySection(all, section);
            if (items.isEmpty()) {
                continue;
            }
            TextView sectionTitle = Ui.text(requireContext(), section, 0xFFFFFFFF, 17f, true, Gravity.START);
            sectionTitle.setPadding(Ui.dp(requireContext(), 10), Ui.dp(requireContext(), 14),
                    Ui.dp(requireContext(), 10), Ui.dp(requireContext(), 8));
            sectionTitle.setBackground(Ui.gradient(Ui.color(requireContext(), R.color.primary),
                    Ui.color(requireContext(), R.color.secondary), 14, requireContext()));
            container.addView(sectionTitle);

            LinearLayout row = null;
            for (int i = 0; i < items.size(); i++) {
                if (i % 2 == 0) {
                    row = new LinearLayout(requireContext());
                    row.setOrientation(LinearLayout.HORIZONTAL);
                    LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT, 0);
                    rp.weight = 1;
                    rp.setMargins(0, 0, Ui.dp(requireContext(), 6), 0);
                    container.addView(row, rp);
                }
                Commands.Cmd cmd = items.get(i);
                row.addView(tile(cmd), new LinearLayout.LayoutParams(0, Ui.dp(requireContext(), 210), 1f));
            }
            if (items.size() % 2 == 1) {
                View spacer = new View(requireContext());
                spacer.setLayoutParams(new LinearLayout.LayoutParams(0, Ui.dp(requireContext(), 210), 1f));
                row.addView(spacer);
            }
        }
    }

    private View tile(Commands.Cmd cmd) {
        MaterialCardView card = new MaterialCardView(requireContext());
        card.setRadius(Ui.dp(requireContext(), 24));
        card.setCardElevation(4f);
        card.setStrokeWidth(Ui.dp(requireContext(), 1));
        card.setStrokeColor(ContextCompat.getColor(requireContext(), R.color.card_stroke));
        card.setCardBackgroundColor(0xFFFFFFFF);

        LinearLayout inner = new LinearLayout(requireContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
        inner.setPadding(Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 16));

        int sectionColor = sectionColor(cmd.section);
        inner.setBackground(Ui.gradient(sectionColor, sectionColor == Ui.color(requireContext(), R.color.primary)
                        ? Ui.color(requireContext(), R.color.secondary) : Ui.color(requireContext(), R.color.primary),
                24, requireContext()));

        TextView icon = Ui.text(requireContext(), cmd.emoji, 0xFFFFFFFF, 30f, false, Gravity.START);
        icon.setPadding(0, 0, 0, Ui.dp(requireContext(), 6));

        TextView title = Ui.text(requireContext(), cmd.title, 0xFFFFFFFF, 16f, true, Gravity.START);
        title.setLineSpacing(2f, 1.05f);

        TextView subtitle = Ui.text(requireContext(), cmd.subtitle,
                0xCCFFFFFF, 12f, false, Gravity.START);
        subtitle.setPadding(0, Ui.dp(requireContext(), 2), 0, 0);

        TextView device = Ui.text(requireContext(), cmd.deviceId != null ? deviceRu(cmd.deviceId) : specialLabel(cmd),
                0xB3FFFFFF, 11f, true, Gravity.START);

        inner.addView(icon);
        inner.addView(title);
        inner.addView(subtitle);
        inner.addView(device);

        card.addView(inner, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT));

        card.setOnClickListener(v -> runTile(cmd));
        return card;
    }

    private int sectionColor(String section) {
        switch (section) {
            case "ПК":
                return Ui.color(requireContext(), R.color.primary);
            case "Роутер":
                return Ui.color(requireContext(), R.color.secondary);
            case "Телефон":
                return Ui.color(requireContext(), R.color.accent);
            case "ВМ":
                return Ui.color(requireContext(), R.color.warning) == 0xFFF59E0B ? 0xFFF59E0B : 0xFFEAB308;
            default:
                return Ui.color(requireContext(), R.color.success);
        }
    }

    private String deviceRu(String id) {
        switch (id) {
            case "pc-desktop": return "ПК";
            case "router": return "Роутер";
            case "phone": return "Телефон";
            case "phone-screen": return "Экран телефона";
            case "vm-desktop": return "ВМ";
            default: return id;
        }
    }

    private String specialLabel(Commands.Cmd cmd) {
        if (cmd.openFiles) return "SSD роутера";
        if (cmd.pickModel) return "Планировщик";
        return "";
    }

    private void runTile(Commands.Cmd cmd) {
        if (cmd.openFiles) {
            ((MainActivity) requireActivity()).showFiles();
            return;
        }
        if (cmd.pickModel) {
            pickModel();
            return;
        }
        if ("notify.send".equals(cmd.action) && cmd.args == null) {
            notifyDialog(cmd);
            return;
        }
        runCmd(cmd);
    }

    /* ─────────────────── уведомление (ввод текста) ─────────────────── */

    private void notifyDialog(Commands.Cmd cmd) {
        LinearLayout root = new LinearLayout(requireContext());
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(Ui.dp(requireContext(), 20), Ui.dp(requireContext(), 20),
                Ui.dp(requireContext(), 20), Ui.dp(requireContext(), 20));
        TextView t1 = Ui.text(requireContext(), "Заголовок", Ui.color(requireContext(), R.color.text), 14f, true, Gravity.START);
        EditTextEx et1 = new EditTextEx(requireContext());
        et1.setHint("например, Ассистент");
        et1.setSingleLine();
        TextView t2 = Ui.text(requireContext(), "Текст", Ui.color(requireContext(), R.color.text), 14f, true, Gravity.START);
        t2.setPadding(0, Ui.dp(requireContext(), 10), 0, 0);
        EditTextEx et2 = new EditTextEx(requireContext());
        et2.setHint("сообщение");
        root.addView(t1);
        root.addView(et1);
        root.addView(t2);
        root.addView(et2);

        new AlertDialog.Builder(requireContext())
                .setTitle("Уведомление на телефоне")
                .setView(root)
                .setPositiveButton("Отправить", (d, w) -> {
                    JSONObject args = new JSONObject();
                    try {
                        args.put("title", et1.getText().toString().trim());
                        args.put("text", et2.getText().toString().trim());
                    } catch (JSONException ignored) {
                    }
                    runCmd(new Commands.Cmd(cmd.section, cmd.title, cmd.subtitle, cmd.deviceId, cmd.action,
                            cmd.emoji, args, cmd.danger, cmd.openFiles, cmd.pickModel));
                })
                .setNegativeButton("Отмена", null)
                .show();
    }

    /* ─────────────────── выбор модели ИИ ─────────────────── */

    private void pickModel() {
        Api.get(requireContext(), "/models", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                String current = body.optString("current", "");
                JSONArray arr = body.optJSONArray("models");
                List<String> models = new ArrayList<>();
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        try {
                            models.add(arr.getString(i));
                        } catch (JSONException ignored) {
                        }
                    }
                }
                if (models.isEmpty()) {
                    Ui.toast(requireContext(), "Список моделей пуст");
                    return;
                }
                String[] items = models.toArray(new String[0]);
                int checked = 0;
                for (int i = 0; i < items.length; i++) {
                    if (items[i].equals(current)) {
                        checked = i;
                        break;
                    }
                }
                new AlertDialog.Builder(requireContext())
                        .setTitle("Модель ИИ")
                        .setSingleChoiceItems(items, checked, (d, which) -> {
                            String sel = items[which];
                            d.dismiss();
                            setModel(sel);
                        })
                        .setNegativeButton("Отмена", null)
                        .show();
            }

            @Override
            public void fail(String message) {
                Ui.toast(requireContext(), "Не удалось получить модели: " + message);
            }
        });
    }

    private void setModel(String model) {
        try {
            Api.post(requireContext(), "/models", new JSONObject().put("model", model), new Api.Cb() {
                @Override
                public void ok(JSONObject body) {
                    Ui.toast(requireContext(), "Модель: " + model);
                }

                @Override
                public void fail(String message) {
                    Ui.toast(requireContext(), "Не удалось сменить модель: " + message);
                }
            });
        } catch (JSONException ignored) {
        }
    }

    /* ─────────────────── выполнение команды ─────────────────── */

    private void runCmd(Commands.Cmd cmd) {
        JSONObject args = cmd.args != null ? cmd.args : new JSONObject();
        JSONObject body = new JSONObject();
        try {
            body.put("device", cmd.deviceId);
            body.put("action", cmd.action);
            body.put("args", args);
            body.put("confirm", false);
        } catch (JSONException ignored) {
        }

        Api.post(requireContext(), "/cmd", body, new Api.Cb() {
            @Override
            public void ok(JSONObject resp) {
                String status = resp.optString("status", "");
                if ("awaiting_confirmation".equals(status)) {
                    showConfirm(cmd, resp.optString("token", ""), resp.optString("question", ""));
                } else {
                    showResult(cmd.title, resp.optString("result", "").toString());
                }
            }

            @Override
            public void fail(String message) {
                Ui.toast(requireContext(), "Команда не выполнена: " + message);
            }
        });
    }

    private void showConfirm(Commands.Cmd cmd, String token, String question) {
        new AlertDialog.Builder(requireContext())
                .setTitle("Подтверждение")
                .setMessage(question + "\n\n" + cmd.deviceId + " → " + cmd.action)
                .setPositiveButton("Подтвердить", (d, w) -> {
                    try {
                        Api.post(requireContext(), "/confirm/" + token + "?session="
                                + new Prefs(requireContext()).session() + "&source=android",
                                new JSONObject(), new Api.Cb() {
                                    @Override
                                    public void ok(JSONObject body) {
                                        Ui.toast(requireContext(), "Готово: " + body.optString("action", cmd.action));
                                    }

                                    @Override
                                    public void fail(String message) {
                                        Ui.toast(requireContext(), "Не удалось подтвердить: " + message);
                                    }
                                });
                    } catch (Exception ignored) {
                    }
                })
                .setNegativeButton("Отмена", null)
                .show();
    }

    private void showResult(String title, String result) {
        if (result == null || result.isEmpty()) {
            Ui.toast(requireContext(), "Выполнено: " + title);
            return;
        }
        TextView tv = new TextView(requireContext());
        tv.setText(result);
        tv.setTextColor(Ui.color(requireContext(), R.color.text));
        tv.setTextSize(14f);
        tv.setPadding(Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 16));
        tv.setLineSpacing(2f, 1.05f);
        ScrollView sv = new ScrollView(requireContext());
        sv.addView(tv);
        new AlertDialog.Builder(requireContext())
                .setTitle("Результат: " + title)
                .setView(sv)
                .setPositiveButton("Закрыть", null)
                .show();
    }

    /* ─────────────────── вспомогательные вью (чтобы не тянуть лишние зависимости) ─────────────────── */

    static final class ImageViewEx extends androidx.appcompat.widget.AppCompatImageView {
        public enum ScaleType { CENTER_CROP }
        public ImageViewEx(android.content.Context c) { super(c); }
        public void setScaleType(ScaleType st) { super.setScaleType(android.widget.ImageView.ScaleType.CENTER_CROP); }
    }

    static final class EditTextEx extends androidx.appcompat.widget.AppCompatEditText {
        public EditTextEx(android.content.Context c) { super(c); }
        {
            setInputType(InputType.TYPE_CLASS_TEXT);
            setTextColor(ContextCompat.getColor(getContext(), R.color.text));
            setHintTextColor(ContextCompat.getColor(getContext(), R.color.muted));
        }
    }

    static final class MaterialCardView extends com.google.android.material.card.MaterialCardView {
        public MaterialCardView(android.content.Context c) { super(c); }
    }
}
