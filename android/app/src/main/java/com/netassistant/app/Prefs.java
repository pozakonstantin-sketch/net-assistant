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
        return sp.getString("server", "http://192.168.1.161:8787");
    }

    public void setServer(String value) {
        sp.edit().putString("server", value).apply();
    }

    public String token() {
        return sp.getString("token", "WyLIFvxJ8rjTx16BUWMPu2bLGLUdBBclO9xB7MNAHaQ");
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
