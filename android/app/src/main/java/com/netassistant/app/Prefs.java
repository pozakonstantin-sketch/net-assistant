package com.netassistant.app;

import android.content.Context;
import android.content.SharedPreferences;

public final class Prefs {
    private static final String NAME = "net_assistant";
    private final SharedPreferences sp;

    public Prefs(Context context) {
        sp = context.getApplicationContext().getSharedPreferences(NAME, Context.MODE_PRIVATE);
    }

    public String server() {
        // Адрес не вшиваем: сервер и токен вводятся на экране входа.
        return sp.getString("server", "");
    }

    public void setServer(String value) {
        sp.edit().putString("server", value).apply();
    }

    public String token() {
        // Токен хранится только на устройстве: дефолт пустой, иначе секрет
        // оказался бы в репозитории.
        return sp.getString("token", "");
    }

    public void setToken(String value) {
        sp.edit().putString("token", value).apply();
    }

    public String session() {
        return sp.getString("session", "");
    }

    public void setSession(String value) {
        sp.edit().putString("session", value).apply();
    }

    public boolean tts() {
        return sp.getBoolean("tts", true);
    }

    public void setTts(boolean value) {
        sp.edit().putBoolean("tts", value).apply();
    }

    public boolean loggedIn() {
        return !server().isEmpty() && !token().isEmpty();
    }
}
