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

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public class DevicesFragment extends Fragment {
    private RecyclerView rv;
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
        // «Обновить» — обычная кнопка в макете, не SwipeRefreshLayout.
        View refreshButton = root.findViewById(R.id.devices_refresh);
        if (refreshButton != null) {
            refreshButton.setOnClickListener(v -> load());
        }
        rv.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new DeviceAdapter(requireContext(), devices, this::openDevice, this::showNetInfoSheet);
        rv.setAdapter(adapter);
        rv.setNestedScrollingEnabled(false);
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
                // Дальше — вся сеть: клиенты роутера и узлы Tailscale
                // (то же, что бот показывает по /net и ИИ видит через net.devices).
                Api.get(requireContext(), "/network", new Api.Cb() {
                    @Override
                    public void ok(JSONObject net) {
                        appendNetwork(net);
                        adapter.notifyDataSetChanged();
                        rootView.findViewById(R.id.devices_empty)
                                .setVisibility(devices.isEmpty() ? View.VISIBLE : View.GONE);
                    }

                    @Override
                    public void fail(String message) {
                        // карта сети недоступна — показываем хотя бы агентов
                        adapter.notifyDataSetChanged();
                        rootView.findViewById(R.id.devices_empty)
                                .setVisibility(devices.isEmpty() ? View.VISIBLE : View.GONE);
                        Ui.toast(requireContext(), "Сеть: " + message);
                    }
                });
            }

            @Override
            public void fail(String message) {
                Ui.toast(requireContext(), "Устройства: " + message);
            }
        });
    }

    /** Дозаполнить список устройствами из LAN и Tailscale. */
    private void appendNetwork(JSONObject net) {
        if (net == null) return;
        JSONObject lan = net.optJSONObject("lan");
        JSONArray clients = lan != null ? lan.optJSONArray("clients") : null;
        if (clients != null) {
            int limit = Math.min(clients.length(), 40);
            for (int i = 0; i < limit; i++) {
                try {
                    JSONObject c = clients.getJSONObject(i);
                    String mac = c.optString("mac", "");
                    String ip = c.optString("ip", "");
                    String host = c.optString("hostname", "");
                    String id = !ip.isEmpty() ? ip : mac;
                    String name = !host.isEmpty() && !"*".equals(host) ? host : id;
                    String platform = c.optBoolean("wifi", false) ? "lan-wifi" : "lan-wired";
                    devices.add(new Device(id, name, platform, true, new ArrayList<>(), 0));
                } catch (JSONException ignored) {
                }
            }
        }
        JSONObject ts = net.optJSONObject("tailscale");
        JSONArray peers = ts != null ? ts.optJSONArray("peers") : null;
        if (peers != null) {
            int limit = Math.min(peers.length(), 40);
            for (int i = 0; i < limit; i++) {
                try {
                    JSONObject p = peers.getJSONObject(i);
                    devices.add(new Device(p.optString("ip", ""), p.optString("name", "узел"),
                            "tailscale", p.optBoolean("online", false),
                            new ArrayList<>(), 0));
                } catch (JSONException ignored) {
                }
            }
        }
    }

    /** Шторка с информацией о «просто подключённом» устройстве (без действий). */
    private void showNetInfoSheet(Device d) {
        Dialog sheet = new Dialog(requireContext(), R.style.Theme_NetAssistant);
        sheet.setContentView(R.layout.dialog_device_sheet);
        TextView title = sheet.findViewById(R.id.sheet_title);
        String kind;
        if ("tailscale".equals(d.platform)) {
            kind = "Tailscale-узел";
        } else if ("lan-wifi".equals(d.platform)) {
            kind = "WiFi-клиент";
        } else {
            kind = "Проводное устройство";
        }
        title.setText(d.name + "  ·  " + kind);

        LinearLayout list = sheet.findViewById(R.id.sheet_list);
        TextView info = Ui.text(requireContext(),
                "Адрес: " + d.deviceId
                        + "\nСостояние: " + (d.online ? "в сети" : "не в сети")
                        + "\n\nПодробности (MAC, сигнал, история доступов) — в веб-панели "
                        + "или у бота: /net. Проверить связь можно у ассистента: "
                        + "«пингани " + d.deviceId + "».",
                Ui.color(requireContext(), R.color.muted), 13f, false, android.view.Gravity.START);
        int pad = Ui.dp(requireContext(), 12);
        info.setPadding(0, pad, 0, pad);
        list.addView(info);

        Button close = new Button(requireContext());
        close.setText("Закрыть");
        close.setOnClickListener(v -> sheet.dismiss());
        list.addView(close);
        sheet.show();
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

    /** Команда шторки устройства: человеческая подпись + действие (+ готовые аргументы). */
    private static final class Cmd {
        final String label;
        final String action;
        final JSONObject args;
        final boolean danger;

        Cmd(String label, String action) {
            this(label, action, new JSONObject(), false);
        }

        Cmd(String label, String action, JSONObject args, boolean danger) {
            this.label = label;
            this.action = action;
            this.args = args != null ? args : new JSONObject();
            this.danger = danger;
        }
    }

    private static JSONObject cmdArgs(String key, Object value) {
        JSONObject args = new JSONObject();
        try {
            args.put(key, value);
        } catch (JSONException ignored) {
        }
        return args;
    }

    private static JSONObject cmdArgs(String key1, Object value1, String key2, Object value2) {
        JSONObject args = cmdArgs(key1, value1);
        try {
            args.put(key2, value2);
        } catch (JSONException ignored) {
        }
        return args;
    }

    /** Полезные команды конкретного типа устройства, в порядке важности. */
    private List<Cmd> curated(String platform) {
        List<Cmd> list = new ArrayList<>();
        if ("pc-desktop".equals(platform)) {
            list.add(new Cmd("📸 Скриншот экрана", "sys.screenshot"));
            list.add(new Cmd("🔒 Заблокировать экран", "sys.lock"));
            list.add(new Cmd("ℹ️ Сведения о ПК", "sys.info"));
            list.add(new Cmd("📶 Состояние сети", "net.status"));
            list.add(new Cmd("📋 Процессы (едят память)", "proc.list",
                    cmdArgs("sort", "memory", "limit", 15), false));
            list.add(new Cmd("🔊 Громкость…", "media.volume", null, false));
            // — питание —
            list.add(new Cmd("💤 Усыпить ПК", "power.action", cmdArgs("action", "sleep"), true));
            list.add(new Cmd("🔄 Перезагрузить ПК", "power.action", cmdArgs("action", "restart"), true));
            list.add(new Cmd("⏻ Выключить ПК полностью", "power.action", cmdArgs("action", "shutdown"), true));
            list.add(new Cmd("✋ Отменить выключение", "power.action", cmdArgs("action", "cancel"), false));
        } else if ("router".equals(platform)) {
            list.add(new Cmd("📋 Клиенты сети", "router.clients"));
            list.add(new Cmd("📊 Отчёт о роутере", "router.report"));
            list.add(new Cmd("📶 Состояние сети", "net.status"));
            list.add(new Cmd("🏓 Проверить связь (пинг)…", "net.ping", null, false));
            list.add(new Cmd("🛜 Включить Wi-Fi", "router.wifi", cmdArgs("enabled", true), true));
            list.add(new Cmd("📴 Выключить Wi-Fi", "router.wifi", cmdArgs("enabled", false), true));
            list.add(new Cmd("🚫 Отключить клиента…", "router.kick", null, true));
            list.add(new Cmd("ℹ️ Перезагрузить роутер", "router.reboot", new JSONObject(), true));
        } else if ("vm-desktop".equals(platform)) {
            list.add(new Cmd("ℹ️ О ВМ", "vm.info"));
            list.add(new Cmd("📸 Скриншот рабочего стола", "vm.screenshot"));
            list.add(new Cmd("🪟 Список окон", "vm.windows"));
            list.add(new Cmd("🛰 Узлы Tailscale", "tailscale.peers"));
            list.add(new Cmd("🌐 Открыть панель в браузере ВМ", "vm.panel", cmdArgs("screen", "chat"), false));
            list.add(new Cmd("🔗 Открыть ссылку…", "vm.open", null, false));
        } else if ("phone-screen".equals(platform)) {
            list.add(new Cmd("🎯 Элементы экрана", "screen.ui"));
            list.add(new Cmd("📸 Снимок экрана", "screen.shot"));
            list.add(new Cmd("💡 Состояние экрана", "screen.info"));
            list.add(new Cmd("🚀 Открыть приложение…", "app.open", null, false));
            list.add(new Cmd("📍 Что открыто сейчас", "app.current"));
        } else {
            // обычный агент телефона и другие платформы
            list.add(new Cmd("🔋 Заряд батареи", "battery.status"));
            list.add(new Cmd("ℹ️ Сведения", "sys.info"));
            list.add(new Cmd("📶 Состояние сети", "net.status"));
            list.add(new Cmd("📱 Список приложений", "apps.list"));
            list.add(new Cmd("🚀 Открыть приложение…", "app.open", null, false));
        }
        return list;
    }

    private void showDeviceSheet(Device device, JSONObject actions) {
        Dialog sheet = new Dialog(requireContext(), R.style.Theme_NetAssistant);
        sheet.setContentView(R.layout.dialog_device_sheet);
        TextView title = sheet.findViewById(R.id.sheet_title);
        String state = device.online ? "в сети" : "не в сети";
        title.setText(device.name + "  ·  " + deviceRu(device.platform));

        LinearLayout list = sheet.findViewById(R.id.sheet_list);

        // 1. Подобранные полезные команды (только доступные этому устройству)
        boolean any = false;
        for (Cmd c : curated(device.platform)) {
            if (!device.actions.contains(c.action)) {
                continue;
            }
            any = true;
            Button b = new Button(requireContext());
            b.setText(c.label);
            b.setAllCaps(false);
            b.setTextSize(15f);
            b.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
            b.setPadding(Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 12),
                    Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 12));
            b.setTextColor(ContextCompat.getColor(requireContext(),
                    c.danger ? R.color.danger : R.color.text));
            b.setOnClickListener(v -> {
                sheet.dismiss();
                runCurated(device, c);
            });
            list.addView(b);
        }
        if (!any) {
            TextView empty = Ui.text(requireContext(), "Нет доступных действий",
                    Ui.color(requireContext(), R.color.muted), 14f, false, android.view.Gravity.CENTER);
            empty.setPadding(0, Ui.dp(requireContext(), 16), 0, Ui.dp(requireContext(), 16));
            list.addView(empty);
        }

        // 2. Остальные команды — компактно внизу, «для всех остальных случаев»
        List<String> other = new ArrayList<>();
        for (String name : device.actions) {
            boolean curated = false;
            for (Cmd c : curated(device.platform)) {
                if (c.action.equals(name)) {
                    curated = true;
                    break;
                }
            }
            if (!curated) {
                other.add(name);
            }
        }
        if (!other.isEmpty()) {
            TextView more = Ui.text(requireContext(), "Другие команды (для ИИ и продвинутых):",
                    Ui.color(requireContext(), R.color.muted), 12f, true, android.view.Gravity.START);
            more.setPadding(Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 14), 0, Ui.dp(requireContext(), 4));
            list.addView(more);
            for (String name : other) {
                JSONObject entry = actions.optJSONObject(name);
                if (entry == null || entry.optBoolean("full_only") && !device.actions.contains(name)) {
                    continue;
                }
                LinearLayout row = new LinearLayout(requireContext());
                row.setOrientation(LinearLayout.HORIZONTAL);
                row.setGravity(android.view.Gravity.CENTER_VERTICAL);
                row.setPadding(Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 4),
                        Ui.dp(requireContext(), 16), Ui.dp(requireContext(), 4));
                String desc = entry.optString("desc", "");
                TextView t = Ui.text(requireContext(), actionRu(name, desc),
                        Ui.color(requireContext(), R.color.muted), 13f, false, android.view.Gravity.START);
                LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0,
                        ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                row.addView(t, tp);
                Button run = new Button(requireContext());
                run.setText("→");
                run.setAllCaps(false);
                run.setTextSize(12f);
                run.setTextColor(ContextCompat.getColor(requireContext(), R.color.primary));
                run.setOnClickListener(v -> {
                    sheet.dismiss();
                    runAction(device.deviceId, name, entry.optJSONObject("args"));
                });
                row.addView(run);
                list.addView(row);
            }
        }

        sheet.show();
    }

    private void runCurated(Device device, Cmd c) {
        if (c.args == null) {
            // нет готовых аргументов — откроем диалог значений
            Api.get(requireContext(), "/actions", new Api.Cb() {
                @Override
                public void ok(JSONObject body) {
                    JSONObject entry = body.optJSONObject(c.action);
                    runAction(device.deviceId, c.action,
                            entry != null ? entry.optJSONObject("args") : new JSONObject());
                }

                @Override
                public void fail(String message) {
                    Ui.toast(requireContext(), "Действия: " + message);
                }
            });
            return;
        }
        if (c.danger) {
            confirmThenRun(device, c);
        } else {
            runCmd(device.deviceId, c.action, c.args, false, c.label);
        }
    }

    private void confirmThenRun(Device device, Cmd c) {
        String question = questionFor(c);
        new AlertDialog.Builder(requireContext())
                .setTitle("Подтверждение")
                .setMessage(question + "\n\nУстройство: " + device.name)
                .setPositiveButton("Да, выполнить", (d, w) ->
                        runCmd(device.deviceId, c.action, c.args, true, c.label))
                .setNegativeButton("Отмена", null)
                .show();
    }

    private String questionFor(Cmd c) {
        String mix = c.action + " " + c.args.optString("action");
        if (mix.contains("shutdown")) {
            return "Выключить ПК полностью? Пока не включите вручную, он будет недоступен.";
        }
        if (mix.contains("restart") && c.action.equals("power.action")) {
            return "Перезагрузить ПК? Он вернётся через 1-2 минуты.";
        }
        if (mix.contains("sleep")) {
            return "Усыпить ПК? Разбудите его кнопкой питания или клавиатурой.";
        }
        if (c.action.equals("router.reboot")) {
            return "Перезагрузить роутер? Интернет и сеть пропадут на 1-2 минуты.";
        }
        if (c.action.equals("router.wifi")) {
            boolean off = !Boolean.TRUE.equals(c.args.opt("enabled"));
            return off
                    ? "Выключить Wi-Fi? Все беспроводные устройства потеряют связь."
                    : "Включить Wi-Fi?";
        }
        if (c.action.equals("router.kick")) {
            return "Отключить устройство по MAC-адресу от Wi-Fi?";
        }
        return "Выполнить «" + c.label + "»?";
    }

    private void showResult(String label, JSONObject resp) {
        JSONObject result = resp.optJSONObject("result");
        StringBuilder sb = new StringBuilder();
        if (result == null || result.length() == 0) {
            sb.append("Готово.");
        } else {
            JSONArray keys = result.names();
            if (keys != null) {
                for (int i = 0; i < keys.length(); i++) {
                    try {
                        String key = keys.getString(i);
                        String value;
                        Object raw = result.opt(key);
                        if (raw instanceof JSONObject) {
                            value = ((JSONObject) raw).toString(2);
                        } else if (raw instanceof JSONArray) {
                            value = ((JSONArray) raw).toString();
                        } else {
                            value = String.valueOf(raw);
                        }
                        if (value.length() > 120) {
                            value = value.substring(0, 120) + "…";
                        }
                        sb.append(key).append(": ").append(value).append("\n");
                    } catch (JSONException ignored) {
                    }
                }
            }
        }
        String text = sb.toString().trim();
        if (text.length() > 3000) {
            text = text.substring(0, 3000) + "\n… (обрезано)";
        }
        new AlertDialog.Builder(requireContext())
                .setTitle(label.replaceAll("^\\S+\\s", ""))
                .setMessage(text.isEmpty() ? "Готово." : text)
                .setPositiveButton("Закрыть", null)
                .show();
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
        runCmd(device, action, new JSONObject(), entryDangerous(argsDef), action);
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
                    runCmd(device, action, args, entryDangerous(argsDef), action);
                })
                .setNegativeButton("Отмена", null)
                .show();
    }

    private boolean entryDangerous(JSONObject entry) {
        return entry != null && entry.optBoolean("dangerous", false);
    }

    private void runCmd(String device, String action, JSONObject args, boolean danger, String label) {
        JSONObject body = new JSONObject();
        try {
            body.put("device", device);
            body.put("action", action);
            body.put("args", args);
            // confirm=true: пользователь уже подтвердил диалогом в приложении
            // (или действие не опасное). Иначе ядро вернёт awaiting_confirmation,
            // и тогда подтвердим «слепым» повторным вызовом.
            body.put("confirm", true);
        } catch (JSONException ignored) {
        }
        Api.post(requireContext(), "/cmd", body, new Api.Cb() {
            @Override
            public void ok(JSONObject resp) {
                String status = resp.optString("status", "");
                if ("awaiting_confirmation".equals(status)) {
                    // Ожидается подтверждение на стороне ядра — просим заново.
                    showConfirm(device, action, args, resp.optString("token", ""),
                            resp.optString("question", ""));
                } else if ("ok".equals(status)) {
                    showResult(label != null ? label : action, resp);
                } else {
                    Ui.toast(requireContext(), "Статус: " + status);
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
                                    public void ok(JSONObject outcome) {
                                        showResult(action, outcome);
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
            case "fs.write": return "Записать файл";
            case "fs.delete": return "Удалить файл";
            case "fs.mkdir": return "Создать каталог";
            case "location.get": return "Местоположение";
            case "media.volume": return "Громкость";
            case "net.status": return "Состояние сети";
            case "net.ping": return "Проверить связь (пинг)";
            case "net.devices": return "Карта сети";
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
            case "power.action": return "Питание ПК (выключить/перезагрузить)";
            case "router.report": return "Отчёт о роутере";
            case "router.clients": return "Клиенты Wi-Fi";
            case "tailscale.peers": return "Узлы Tailscale";
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
        private final java.util.function.Consumer<Device> onInfo;

        DeviceAdapter(android.content.Context c, List<Device> items, java.util.function.Consumer<Device> onOpen,
                      java.util.function.Consumer<Device> onInfo) {
            this.c = c;
            this.items = items;
            this.onOpen = onOpen;
            this.onInfo = onInfo;
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
                boolean netRow = d.platform.equals("lan-wifi") || d.platform.equals("lan-wired")
                        || d.platform.equals("tailscale");
                int iconRes;
                if (netRow) {
                    iconRes = d.platform.equals("lan-wired") ? R.drawable.device_pc
                            : R.drawable.device_router;
                } else {
                    switch (d.platform) {
                        case "router": iconRes = R.drawable.device_router; break;
                        case "phone": iconRes = R.drawable.device_phone; break;
                        case "phone-screen": iconRes = R.drawable.device_phone; break;
                        case "vm-desktop": iconRes = R.drawable.device_vm; break;
                        default: iconRes = R.drawable.device_pc;
                    }
                }
                icon.setImageResource(iconRes);
                name.setText(d.name);
                String label;
                if ("tailscale".equals(d.platform)) {
                    label = "Tailscale-узел";
                } else if ("lan-wifi".equals(d.platform)) {
                    label = "Клиент WiFi";
                } else if ("lan-wired".equals(d.platform)) {
                    label = "Проводное устройство";
                } else {
                    label = deviceRu(d.platform) + "  ·  " + d.actions.size() + " действий";
                }
                platform.setText(label);
                String status = d.online ? "в сети" : "не в сети";
                if (d.lastSeen > 0 && d.online) {
                    long ago = (long) (System.currentTimeMillis() / 1000.0 - d.lastSeen);
                    if (ago < 60) {
                        status += " • только что";
                    } else {
                        status += " • " + ago + " с назад";
                    }
                }
                meta.setText(status);
                dot.setBackgroundResource(d.online ? R.drawable.bg_dot_online : R.drawable.bg_dot_offline);
                row.setOnClickListener(v -> {
                    if (netRow) {
                        onInfo.accept(d);
                    } else {
                        onOpen.accept(d);
                    }
                });
            }
        }
    }
}
