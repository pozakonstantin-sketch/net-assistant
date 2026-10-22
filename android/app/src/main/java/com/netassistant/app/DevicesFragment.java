package com.netassistant.app;

import android.app.AlertDialog;
import android.app.Dialog;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public class DevicesFragment extends Fragment {
    private RecyclerView rv;
    private SwipeRefreshLayout refresh;
    private DeviceAdapter adapter;
    private View rootView;
    private List<Device> devices = new ArrayList<>();

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_devices, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View root, @Nullable Bundle savedInstanceState) {
        rootView = root;
        rv = root.findViewById(R.id.devices);
        refresh = root.findViewById(R.id.devices_refresh);
        rv.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new DeviceAdapter(requireContext(), devices, this::openDevice);
        rv.setAdapter(adapter);
        rv.setNestedScrollingEnabled(false);

        refresh.setOnClickListener(v -> load());
        load();
    }

    @Override
    public void onResume() {
        super.onResume();
        load();
    }

    private void load() {
        Api.get(requireContext(), "/devices", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                devices.clear();
                JSONArray arr = body.optJSONArray("devices");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        try {
                            JSONObject o = arr.getJSONObject(i);
                            // available_actions — уже отфильтрованный по каталогу
                            // список (как в веб-панели), а «actions» агентский
                            // полный набор: команды из него ядро бы отклоняло.
                            JSONArray acts = o.optJSONArray("available_actions");
                            List<String> actsList = new ArrayList<>();
                            if (acts != null) {
                                for (int j = 0; j < acts.length(); j++) {
                                    actsList.add(acts.getString(j));
                                }
                            }
                            devices.add(new Device(o.optString("device_id", ""), o.optString("name", ""),
                                    o.optString("platform", ""), o.optBoolean("online", false),
                                    actsList, o.optDouble("last_seen", 0)));
                        } catch (JSONException ignored) {
                        }
                    }
                }
                adapter.notifyDataSetChanged();
                rootView.findViewById(R.id.devices_empty).setVisibility(devices.isEmpty() ? View.VISIBLE : View.GONE);
            }

            @Override
            public void fail(String message) {
                Ui.toast(requireContext(), "Устройства: " + message);
            }
        });
    }

    private void openDevice(Device device) {
        Api.get(requireContext(), "/actions", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                showDeviceSheet(device, body);
            }

            @Override
            public void fail(String message) {
                Ui.toast(requireContext(), "Действия: " + message);
            }
        });
    }

    private void showDeviceSheet(Device device, JSONObject actions) {
        Dialog sheet = new Dialog(requireContext(), R.style.Theme_NetAssistant);
        sheet.setContentView(R.layout.dialog_device_sheet);
        TextView title = sheet.findViewById(R.id.sheet_title);
        title.setText(device.name + "  ·  " + deviceRu(device.platform));

        LinearLayout list = sheet.findViewById(R.id.sheet_list);
        List<String> names = device.actions;
        if (names.isEmpty()) {
            TextView empty = Ui.text(requireContext(), "Нет доступных действий",
                    Ui.color(requireContext(), R.color.muted), 14f, false, android.view.Gravity.CENTER);
            empty.setPadding(0, Ui.dp(requireContext(), 16), 0, Ui.dp(requireContext(), 16));
            list.addView(empty);
        }
        for (String name : names) {
            JSONObject entry = actions.optJSONObject(name);
            String desc = entry != null ? entry.optString("desc", "") : "";
            String ru = actionRu(name, desc);
            JSONObject args = entry != null ? entry.optJSONObject("args") : new JSONObject();

            LinearLayout row = new LinearLayout(requireContext());
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(android.view.Gravity.CENTER_VERTICAL);
            row.setPadding(Ui.dp(requireContext(), 12), Ui.dp(requireContext(), 10),
                    Ui.dp(requireContext(), 12), Ui.dp(requireContext(), 10));

            TextView t = Ui.text(requireContext(), ru, Ui.color(requireContext(), R.color.text), 15f, false, android.view.Gravity.START);
            LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            tp.setMargins(0, 0, Ui.dp(requireContext(), 10), 0);
            row.addView(t, tp);

            TextView sub = Ui.text(requireContext(), name + "  " + (entry != null && entry.optBoolean("dangerous") ? "⚠" : ""),
                    Ui.color(requireContext(), R.color.muted), 11f, false, android.view.Gravity.START);
            sub.setMaxLines(2);
            sub.setEllipsize(android.text.TextUtils.TruncateAt.END);

            LinearLayout left = new LinearLayout(requireContext());
            left.setOrientation(LinearLayout.VERTICAL);
            left.addView(t);
            left.addView(sub);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            row.addView(left, lp);

            Button run = new Button(requireContext());
            run.setText("Выполнить");
            run.setAllCaps(false);
            run.setTextSize(12f);
            run.setTextColor(ContextCompat.getColor(requireContext(), R.color.primary));
            run.setBackground(ContextCompat.getDrawable(requireContext(), R.drawable.bg_chip));
            run.setPadding(Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8), Ui.dp(requireContext(), 14), Ui.dp(requireContext(), 8));
            final JSONObject fArgs = args;
            run.setOnClickListener(v -> {
                sheet.dismiss();
                runAction(device.deviceId, name, fArgs);
            });
            row.addView(run);
            list.addView(row);
        }

        sheet.show();
    }

    private void runAction(String device, String action, JSONObject argsDef) {
        // Сбор аргументов, если есть обязательные
        List<String> required = new ArrayList<>();
        if (argsDef != null) {
            JSONArray keys = argsDef.names();
            if (keys != null) {
                for (int i = 0; i < keys.length(); i++) {
                    try {
                        String k = keys.getString(i);
                        JSONObject a = argsDef.optJSONObject(k);
                        if (a != null && a.optBoolean("required", false)) {
                            required.add(k);
                        }
                    } catch (JSONException ignored) {
                    }
                }
            }
        }
        if (!required.isEmpty()) {
            argsDialog(device, action, argsDef, required);
            return;
        }
        runCmd(device, action, new JSONObject(), entryDangerous(argsDef));
    }

    private void argsDialog(String device, String action, JSONObject argsDef, List<String> required) {
        LinearLayout root = new LinearLayout(requireContext());
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(Ui.dp(requireContext(), 20), Ui.dp(requireContext(), 20),
                Ui.dp(requireContext(), 20), Ui.dp(requireContext(), 20));
        java.util.Map<String, EditText> fields = new java.util.LinkedHashMap<>();
        for (String k : required) {
            JSONObject a = argsDef.optJSONObject(k);
            TextView lbl = Ui.text(requireContext(), k + " (" + (a != null ? a.optString("type", "текст") : "текст") + ")",
                    Ui.color(requireContext(), R.color.text), 13f, true, android.view.Gravity.START);
            EditText et = new EditText(requireContext());
            et.setHint("значение для " + k);
            et.setSingleLine();
            et.setPadding(Ui.dp(requireContext(), 10), Ui.dp(requireContext(), 6),
                    Ui.dp(requireContext(), 10), Ui.dp(requireContext(), 6));
            et.setBackground(ContextCompat.getDrawable(requireContext(), R.drawable.bg_input));
            if (a != null && a.optString("type", "").equals("int")) {
                et.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
            }
            fields.put(k, et);
            root.addView(lbl);
            root.addView(et);
            root.addView(Ui.text(requireContext(), "", 0, 8f, false, android.view.Gravity.START));
        }
        new AlertDialog.Builder(requireContext())
                .setTitle("Параметры: " + action)
                .setView(root)
                .setPositiveButton("Выполнить", (d, w) -> {
                    JSONObject args = new JSONObject();
                    try {
                        for (java.util.Map.Entry<String, EditText> e : fields.entrySet()) {
                            args.put(e.getKey(), e.getValue().getText().toString().trim());
                        }
                        // остальные аргументы — значения по умолчанию
                        if (argsDef != null) {
                            JSONArray keys = argsDef.names();
                            if (keys != null) {
                                for (int i = 0; i < keys.length(); i++) {
                                    String k = keys.getString(i);
                                    if (!args.has(k)) {
                                        JSONObject a = argsDef.optJSONObject(k);
                                        if (a != null && a.has("default")) {
                                            args.put(k, a.get("default"));
                                        }
                                    }
                                }
                            }
                        }
                    } catch (JSONException ignored) {
                    }
                    runCmd(device, action, args, entryDangerous(argsDef));
                })
                .setNegativeButton("Отмена", null)
                .show();
    }

    private boolean entryDangerous(JSONObject entry) {
        return entry != null && entry.optBoolean("dangerous", false);
    }

    private void runCmd(String device, String action, JSONObject args, boolean danger) {
        JSONObject body = new JSONObject();
        try {
            body.put("device", device);
            body.put("action", action);
            body.put("args", args);
            body.put("confirm", !danger);
        } catch (JSONException ignored) {
        }
        Api.post(requireContext(), "/cmd", body, new Api.Cb() {
            @Override
            public void ok(JSONObject resp) {
                String status = resp.optString("status", "");
                if ("awaiting_confirmation".equals(status)) {
                    showConfirm(device, action, args, resp.optString("token", ""), resp.optString("question", ""));
                } else {
                    Ui.toast(requireContext(), "Выполнено: " + action);
                }
            }

            @Override
            public void fail(String message) {
                Ui.toast(requireContext(), "Команда не выполнена: " + message);
            }
        });
    }

    private void showConfirm(String device, String action, JSONObject args, String token, String question) {
        new AlertDialog.Builder(requireContext())
                .setTitle("Подтверждение")
                .setMessage(question + "\n\n" + device + " → " + action)
                .setPositiveButton("Подтвердить", (d, w) -> {
                    JSONObject body = new JSONObject();
                    try {
                        Api.post(requireContext(), "/confirm/" + token + "?session="
                                        + new Prefs(requireContext()).session() + "&source=android",
                                body, new Api.Cb() {
                                    @Override
                                    public void ok(JSONObject body) {
                                        Ui.toast(requireContext(), "Готово: " + action);
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

    private static String deviceRu(String id) {
        switch (id) {
            case "pc-desktop": return "ПК";
            case "router": return "Роутер";
            case "phone": return "Телефон";
            case "phone-screen": return "Экран телефона";
            case "vm-desktop": return "ВМ";
            default: return id;
        }
    }

    private static String actionRu(String name, String desc) {
        switch (name) {
            case "app.current": return "Текущее приложение";
            case "app.info": return "Сведения о приложении";
            case "app.open": return "Открыть приложение";
            case "app.stop": return "Остановить приложение";
            case "apps.list": return "Список приложений";
            case "battery.status": return "Заряд аккумулятора";
            case "exec.run": return "Команда оболочки";
            case "file.list": return "Список файлов";
            case "file.read": return "Прочитать файл";
            case "file.search": return "Поиск в файлах";
            case "fs.list": return "Содержимое каталога";
            case "fs.read": return "Чтение файла";
            case "location.get": return "Местоположение";
            case "media.volume": return "Громкость";
            case "net.status": return "Состояние сети";
            case "notify.send": return "Уведомление";
            case "proc.kill": return "Завершить процесс";
            case "proc.list": return "Процессы";
            case "shell.run": return "Оболочка";
            case "sms.list": return "SMS";
            case "sms.send": return "Отправить SMS";
            case "sys.info": return "Сведения о ПК";
            case "sys.lock": return "Заблокировать экран";
            case "sys.screenshot": return "Скриншот ПК";
            case "torch.set": return "Фонарик";
            case "tts.speak": return "Озвучить текст";
            case "wifi.info": return "Информация о Wi-Fi";
            case "screen.info": return "Состояние экрана";
            case "screen.key": return "Кнопка телефона";
            case "screen.shot": return "Скриншот экрана";
            case "screen.swipe": return "Свайп по экрану";
            case "screen.tap": return "Коснуться экрана";
            case "screen.text": return "Ввести текст";
            case "screen.ui": return "Элементы экрана";
            case "router.clients": return "Клиенты Wi-Fi";
            case "router.kick": return "Отключить клиента";
            case "router.reboot": return "Перезагрузить роутер";
            case "router.wifi": return "Wi-Fi роутера";
            case "vm.click": return "Клик мышью";
            case "vm.info": return "Сведения о ВМ";
            case "vm.key": return "Клавиша";
            case "vm.move": return "Курсор";
            case "vm.open": return "Открыть ссылку";
            case "vm.panel": return "Панель ассистента";
            case "vm.screenshot": return "Скриншот ВМ";
            case "vm.scroll": return "Прокрутка";
            case "vm.type": return "Ввести текст";
            case "vm.windows": return "Окна ВМ";
            default:
                if (desc != null && !desc.isEmpty()) {
                    return desc.split("\\.")[0];
                }
                return name;
        }
    }

    /* ─────────────────── модель и адаптер ─────────────────── */

    public static final class Device {
        public final String deviceId, name, platform;
        public final boolean online;
        public final List<String> actions;
        public final double lastSeen;

        Device(String deviceId, String name, String platform, boolean online,
               List<String> actions, double lastSeen) {
            this.deviceId = deviceId;
            this.name = name;
            this.platform = platform;
            this.online = online;
            this.actions = actions;
            this.lastSeen = lastSeen;
        }
    }

    public static final class DeviceAdapter extends RecyclerView.Adapter<DeviceAdapter.Holder> {
        private final android.content.Context c;
        private final List<Device> items;
        private final java.util.function.Consumer<Device> onOpen;

        DeviceAdapter(android.content.Context c, List<Device> items, java.util.function.Consumer<Device> onOpen) {
            this.c = c;
            this.items = items;
            this.onOpen = onOpen;
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
            final LinearLayout row;
            final android.widget.ImageView icon;
            final TextView name;
            final TextView platform;
            final TextView meta;
            final android.view.View dot;

            Holder(LinearLayout row) {
                super(row);
                this.row = row;
                this.icon = new android.widget.ImageView(c);
                LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(Ui.dp(c, 56), Ui.dp(c, 56));
                ip.setMargins(0, 0, Ui.dp(c, 14), 0);
                row.addView(icon, ip);

                LinearLayout texts = new LinearLayout(c);
                texts.setOrientation(LinearLayout.VERTICAL);
                this.name = Ui.text(c, "", Ui.color(c, R.color.text), 16f, true, android.view.Gravity.START);
                this.platform = Ui.text(c, "", Ui.color(c, R.color.muted), 13f, false, android.view.Gravity.START);
                this.meta = Ui.text(c, "", Ui.color(c, R.color.muted), 12f, false, android.view.Gravity.START);
                texts.addView(name);
                texts.addView(platform);
                texts.addView(meta);
                row.addView(texts, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

                LinearLayout right = new LinearLayout(c);
                right.setOrientation(LinearLayout.VERTICAL);
                right.setGravity(android.view.Gravity.END | android.view.Gravity.CENTER_VERTICAL);
                this.dot = new android.view.View(c);
                LinearLayout.LayoutParams dp = new LinearLayout.LayoutParams(Ui.dp(c, 10), Ui.dp(c, 10));
                dp.setMargins(0, 0, Ui.dp(c, 4), 0);
                right.addView(dot, dp);
                row.addView(right);
            }

            void bind(Device d) {
                int iconRes;
                switch (d.platform) {
                    case "router": iconRes = R.drawable.device_router; break;
                    case "phone": iconRes = R.drawable.device_phone; break;
                    case "phone-screen": iconRes = R.drawable.device_phone; break;
                    case "vm-desktop": iconRes = R.drawable.device_vm; break;
                    default: iconRes = R.drawable.device_pc;
                }
                icon.setImageResource(iconRes);
                name.setText(d.name);
                platform.setText(deviceRu(d.platform) + "  ·  " + d.actions.size() + " действий");
                String status = d.online ? "в сети" : "не в сети";
                long ago = (long) (System.currentTimeMillis() / 1000.0 - d.lastSeen);
                if (d.online && ago < 60) {
                    status += " • только что";
                } else if (d.online) {
                    status += " • " + ago + " с назад";
                }
                meta.setText(status);
                dot.setBackgroundResource(d.online ? R.drawable.bg_dot_online : R.drawable.bg_dot_offline);
                row.setOnClickListener(v -> onOpen.accept(d));
            }
        }
    }
}
