package com.netassistant.app;

import android.app.AlertDialog;
import android.content.Intent;
import android.os.Bundle;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.CompoundButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.widget.SwitchCompat;
import androidx.core.content.ContextCompat;
import com.google.android.material.card.MaterialCardView;
import androidx.fragment.app.Fragment;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public class MoreFragment extends Fragment {
    private LinearLayout container;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_more, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        container = view.findViewById(R.id.more_container);
        build();
    }

    @Override
    public void onResume() {
        super.onResume();
        build(); // перерисуем — könnten измениться настройки
    }

    private void build() {
        container.removeAllViews();

        ImageViewEx hero = new ImageViewEx(requireContext());
        hero.setImageResource(R.drawable.hero_more);
        hero.setScaleType(ImageViewEx.ScaleType.CENTER_CROP);
        container.addView(hero, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, Ui.dp(requireContext(), 170)));

        TextView title = Ui.text(requireContext(), getString(R.string.more_title), 0xFF1B1633, 26f, true, Gravity.START);
        title.setPadding(0, Ui.dp(requireContext(), 14), 0, Ui.dp(requireContext(), 4));
        container.addView(title);

        container.addView(cardServer());
        container.addView(cardModel());
        container.addView(cardTts());
        container.addView(cardFiles());
        container.addView(cardReset());
        container.addView(cardAbout());
    }

    private MaterialCardView cardServer() {
        MaterialCardView card = Ui.card(requireContext(), 0xFF6C4DFF, 0xFF00C2FF, 22, 4f);
        card.setCardBackgroundColor(0xFFFFFFFF);
        LinearLayout inner = new LinearLayout(requireContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setPadding(Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16));
        TextView lbl = Ui.text(requireContext(), "Сервер", Ui.color(requireContext(), R.color.text), 16f, true, Gravity.START);
        TextView val = Ui.text(requireContext(), new Prefs(requireContext()).server(), Ui.color(requireContext(), R.color.muted), 13f, false, Gravity.START);
        val.setPadding(0, Ui.dp(requireContext(), 4), 0, 0);
        ButtonEx change = new ButtonEx(requireContext(), "Изменить сервер и токен", 0xFF6C4DFF);
        change.setPadding(Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8), Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8));
        change.setOnClickListener(v -> startActivity(new Intent(requireContext(), LoginActivity.class)));
        inner.addView(lbl);
        inner.addView(val);
        inner.addView(change);
        card.addView(inner);
        return card;
    }

    private MaterialCardView cardModel() {
        MaterialCardView card = Ui.card(requireContext(), 0xFF00C2FF, 0xFF6C4DFF, 22, 4f);
        card.setCardBackgroundColor(0xFFFFFFFF);
        LinearLayout inner = new LinearLayout(requireContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setPadding(Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16));
        TextView lbl = Ui.text(requireContext(), "Модель ИИ", Ui.color(requireContext(), R.color.text), 16f, true, Gravity.START);
        TextView val = Ui.text(requireContext(), "текущая: загрузка…", Ui.color(requireContext(), R.color.muted), 13f, false, Gravity.START);
        val.setPadding(0, Ui.dp(requireContext(), 4), 0, 0);
        Api.get(requireContext(), "/models", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                val.setText("текущая: " + body.optString("current", "—"));
            }

            @Override
            public void fail(String message) {
                val.setText("текущая: —");
            }
        });
        ButtonEx choose = new ButtonEx(requireContext(), "Выбрать модель", 0xFF00C2FF);
        choose.setPadding(Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8), Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8));
        choose.setOnClickListener(v -> pickModel());
        inner.addView(lbl);
        inner.addView(val);
        inner.addView(choose);
        card.addView(inner);
        return card;
    }

    private MaterialCardView cardTts() {
        MaterialCardView card = Ui.card(requireContext(), 0xFFFF4D8D, 0xFF6C4DFF, 22, 4f);
        card.setCardBackgroundColor(0xFFFFFFFF);
        LinearLayout inner = new LinearLayout(requireContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setPadding(Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16));
        LinearLayout row = new LinearLayout(requireContext());
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView lbl = Ui.text(requireContext(), "Озвучка ответов ИИ", Ui.color(requireContext(), R.color.text), 16f, true, Gravity.START);
        lbl.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        SwitchCompat sw = new SwitchCompat(requireContext());
        sw.setChecked(new Prefs(requireContext()).tts());
        sw.setOnCheckedChangeListener((buttonView, isChecked) -> new Prefs(requireContext()).setTts(isChecked));
        row.addView(lbl);
        row.addView(sw);
        TextView hint = Ui.text(requireContext(), "Отвечать голосом Дмитрий (мужской голос, edge-tts)",
                Ui.color(requireContext(), R.color.muted), 12f, false, Gravity.START);
        inner.addView(row);
        inner.addView(hint);
        card.addView(inner);
        return card;
    }

    private MaterialCardView cardFiles() {
        MaterialCardView card = Ui.card(requireContext(), 0xFF22C55E, 0xFF00C2FF, 22, 4f);
        card.setCardBackgroundColor(0xFFFFFFFF);
        LinearLayout inner = new LinearLayout(requireContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setPadding(Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16));
        TextView lbl = Ui.text(requireContext(), "Файлы на SSD роутера", Ui.color(requireContext(), R.color.text), 16f, true, Gravity.START);
        TextView hint = Ui.text(requireContext(), "Документы, присланные в чат, сохраняются на роутер",
                Ui.color(requireContext(), R.color.muted), 12f, false, Gravity.START);
        hint.setPadding(0, Ui.dp(requireContext(), 4), 0, 0);
        ButtonEx open = new ButtonEx(requireContext(), "Открыть файлы", 0xFF22C55E);
        open.setPadding(Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8), Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8));
        open.setOnClickListener(v -> ((MainActivity) requireActivity()).showFiles());
        inner.addView(lbl);
        inner.addView(hint);
        inner.addView(open);
        card.addView(inner);
        return card;
    }

    private MaterialCardView cardReset() {
        MaterialCardView card = Ui.card(requireContext(), 0xFFF59E0B, 0xFFF59E0B, 22, 4f);
        card.setCardBackgroundColor(0xFFFFFFFF);
        LinearLayout inner = new LinearLayout(requireContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setPadding(Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16));
        TextView lbl = Ui.text(requireContext(), "Очистить переписку", Ui.color(requireContext(), R.color.text), 16f, true, Gravity.START);
        TextView hint = Ui.text(requireContext(), "Удалить историю сессии со всех устройств",
                Ui.color(requireContext(), R.color.muted), 12f, false, Gravity.START);
        hint.setPadding(0, Ui.dp(requireContext(), 4), 0, 0);
        ButtonEx clear = new ButtonEx(requireContext(), "Очистить", 0xFFF59E0B);
        clear.setPadding(Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8), Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8));
        clear.setOnClickListener(v -> new AlertDialog.Builder(requireContext())
                .setTitle("Очистить переписку?")
                .setMessage("История удалится на ВМ и во всех устройствах. Это нельзя отменить.")
                .setPositiveButton("Очистить", (d, w) -> {
                    String session = new Prefs(requireContext()).session();
                    Api.delete(requireContext(), "/session/" + session, new Api.Cb() {
                        @Override
                        public void ok(JSONObject body) {
                            Ui.toast(requireContext(), "Переписка очищена");
                        }

                        @Override
                        public void fail(String message) {
                            Ui.toast(requireContext(), "Не удалось очистить: " + message);
                        }
                    });
                })
                .setNegativeButton("Отмена", null)
                .show());
        inner.addView(lbl);
        inner.addView(hint);
        inner.addView(clear);
        card.addView(inner);
        return card;
    }

    private MaterialCardView cardAbout() {
        MaterialCardView card = Ui.card(requireContext(), 0xFF6F6A8A, 0xFF6F6A8A, 22, 4f);
        card.setCardBackgroundColor(0xFFFFFFFF);
        LinearLayout inner = new LinearLayout(requireContext());
        inner.setOrientation(LinearLayout.VERTICAL);
        inner.setPadding(Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16),
                Ui.dp(requireContext(), 18), Ui.dp(requireContext(), 16));
        TextView lbl = Ui.text(requireContext(), "О приложении", Ui.color(requireContext(), R.color.text), 16f, true, Gravity.START);
        TextView about = Ui.text(requireContext(), "Net Assistant 1.0\nУправление ПК, телефоном и роутером через одного ассистента.\nИсходный код: приватный репозиторий GitHub.",
                Ui.color(requireContext(), R.color.muted), 13f, false, Gravity.START);
        about.setPadding(0, Ui.dp(requireContext(), 4), 0, 0);
        inner.addView(lbl);
        inner.addView(about);
        card.addView(inner);
        return card;
    }

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
                    if (items[i].equals(current)) checked = i;
                }
                new AlertDialog.Builder(requireContext())
                        .setTitle("Модель ИИ")
                        .setSingleChoiceItems(items, checked, (d, which) -> {
                            String sel = items[which];
                            d.dismiss();
                            try {
                                Api.post(requireContext(), "/models", new JSONObject().put("model", sel), new Api.Cb() {
                                    @Override
                                    public void ok(JSONObject b) {
                                        Ui.toast(requireContext(), "Модель: " + sel);
                                        build();
                                    }

                                    @Override
                                    public void fail(String message) {
                                        Ui.toast(requireContext(), "Не удалось сменить модель: " + message);
                                    }
                                });
                            } catch (JSONException ignored) {
                            }
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

    /* ─────────────────── вспомогательные вью ─────────────────── */

    static final class ImageViewEx extends androidx.appcompat.widget.AppCompatImageView {
        public enum ScaleType { CENTER_CROP }
        public ImageViewEx(android.content.Context c) { super(c); }
        public void setScaleType(ScaleType st) { super.setScaleType(android.widget.ImageView.ScaleType.CENTER_CROP); }
    }

    static final class ButtonEx extends androidx.appcompat.widget.AppCompatButton {
        public ButtonEx(android.content.Context c, String text, int color) {
            super(c);
            setText(text);
            setTextColor(0xFFFFFFFF);
            setTextSize(14f);
            setAllCaps(false);
            setBackgroundColor(color);
            setPadding(Ui.dp(c, 14), Ui.dp(c, 8), Ui.dp(c, 14), Ui.dp(c, 8));
        }
    }
}
