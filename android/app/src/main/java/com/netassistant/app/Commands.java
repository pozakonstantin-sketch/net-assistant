package com.netassistant.app;

import android.app.Activity;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public final class Commands {
    private Commands() {
    }

    public static final class Cmd {
        public String section;
        public final String title;
        public final String subtitle;
        public final String deviceId;
        public final String action;
        public final String emoji;
        public final JSONObject args;      // null — спросить у пользователя (notify.send)
        public final boolean danger;       // требует подтверждения
        public final boolean openFiles;    // открыть экран файлов
        public final boolean pickModel;    // выбор модели ИИ

        public Cmd(String section, String title, String subtitle, String deviceId, String action,
                   String emoji, JSONObject args, boolean danger, boolean openFiles, boolean pickModel) {
            this.section = section;
            this.title = title;
            this.subtitle = subtitle;
            this.deviceId = deviceId;
            this.action = action;
            this.emoji = emoji;
            this.args = args;
            this.danger = danger;
            this.openFiles = openFiles;
            this.pickModel = pickModel;
        }
    }

    public static List<Cmd> all() {
        List<Cmd> list = new ArrayList<>();

        JSONObject empty = new JSONObject();
        JSONObject vol30 = put("level", 30);
        JSONObject vol70 = put("level", 70);
        JSONObject mute = put("mute", true);
        JSONObject proc = new JSONObject();
        try {
            proc.put("sort", "memory").put("limit", 10);
        } catch (Exception ignored) {
        }
        JSONObject wifiOff = put("enabled", false);
        JSONObject wifiOn = put("enabled", true);
        JSONObject torchOn = put("on", true);
        JSONObject torchOff = put("on", false);

        // —— ПК ——
        section(list, "ПК", new Cmd("ПК", "Скриншот ПК", "Снимок экрана рабочего стола", "pc-desktop", "sys.screenshot",
                "🖥️", empty, false, false, false));
        section(list, "ПК", new Cmd("ПК", "Заблокировать ПК", "Экран блокировки (как Win+L)", "pc-desktop", "sys.lock",
                "🔒", empty, false, false, false));
        section(list, "ПК", new Cmd("ПК", "Громкость 30%", "Уровень звука на ПК", "pc-desktop", "media.volume",
                "🔊", vol30, false, false, false));
        section(list, "ПК", new Cmd("ПК", "Громкость 70%", "Уровень звука на ПК", "pc-desktop", "media.volume",
                "🔊", vol70, false, false, false));
        section(list, "ПК", new Cmd("ПК", "Звук выкл", "Приглушить звук на ПК", "pc-desktop", "media.volume",
                "🔇", mute, false, false, false));
        section(list, "ПК", new Cmd("ПК", "Процессы", "Топ процессов по памяти", "pc-desktop", "proc.list",
                "📋", proc, false, false, false));
        section(list, "ПК", new Cmd("ПК", "Сведения о ПК", "ОС, процессор, память, аптайм", "pc-desktop", "sys.info",
                "ℹ️", empty, false, false, false));

        // —— Роутер ——
        section(list, "Роутер", new Cmd("Роутер", "Кто в Wi-Fi", "Подключённые устройства", "router", "router.clients",
                "📶", empty, false, false, false));
        section(list, "Роутер", new Cmd("Роутер", "Wi-Fi выкл", "Выключить радиомодуль", "router", "router.wifi",
                "📴", wifiOff, true, false, false));
        section(list, "Роутер", new Cmd("Роутер", "Wi-Fi вкл", "Включить радиомодуль", "router", "router.wifi",
                "📶", wifiOn, true, false, false));
        section(list, "Роутер", new Cmd("Роутер", "Перезагрузить роутер", "Связь пропадёт на 1–2 минуты", "router",
                "router.reboot", "🔄", empty, true, false, false));

        // —— Телефон ——
        section(list, "Телефон", new Cmd("Телефон", "Заряд телефона", "Состояние аккумулятора", "phone", "battery.status",
                "🔋", empty, false, false, false));
        section(list, "Телефон", new Cmd("Телефон", "Фонарик вкл", "Включить фонарик телефона", "phone", "torch.set",
                "🔦", torchOn, false, false, false));
        section(list, "Телефон", new Cmd("Телефон", "Фонарик выкл", "Выключить фонарик телефона", "phone", "torch.set",
                "🌑", torchOff, false, false, false));
        section(list, "Телефон", new Cmd("Телефон", "Уведомление", "Показать уведомление на телефоне", "phone",
                "notify.send", "🔔", null, false, false, false));
        section(list, "Телефон", new Cmd("Телефон", "Скриншот экрана", "Снимок экрана Honor", "phone-screen",
                "screen.shot", "📷", empty, false, false, false));

        // —— ВМ ——
        section(list, "ВМ", new Cmd("ВМ", "Скриншот ВМ", "Рабочий стол Astra Linux", "vm-desktop", "vm.screenshot",
                "🖥️", empty, false, false, false));
        section(list, "ВМ", new Cmd("ВМ", "Открыть панель", "Интерфейс ассистента в браузере ВМ", "vm-desktop", "vm.panel",
                "🧭", empty, false, false, false));
        section(list, "ВМ", new Cmd("ВМ", "Окна ВМ", "Список открытых окон", "vm-desktop", "vm.windows",
                "🪟", empty, false, false, false));

        // —— Файлы и ИИ ——
        section(list, "Файлы и ИИ", new Cmd("Файлы и ИИ", "Мои файлы", "Присланные документы на SSD роутера", null,
                null, "📁", empty, false, true, false));
        section(list, "Файлы и ИИ", new Cmd("Файлы и ИИ", "Модель ИИ", "Переключить модель планировщика", null, null,
                "🤖", empty, false, false, true));

        return list;
    }

    private static void section(List<Cmd> list, String section, Cmd cmd) {
        cmd.section = section;
        list.add(cmd);
    }

    private static JSONObject put(String k, Object v) {
        JSONObject o = new JSONObject();
        try {
            o.put(k, v);
        } catch (Exception ignored) {
        }
        return o;
    }

    public static List<Cmd> bySection(List<Cmd> all, String section) {
        List<Cmd> out = new ArrayList<>();
        for (Cmd c : all) {
            if (c.section.equals(section)) {
                out.add(c);
            }
        }
        return out;
    }

    public static String[] sections() {
        return new String[]{"ПК", "Роутер", "Телефон", "ВМ", "Файлы и ИИ"};
    }
}
