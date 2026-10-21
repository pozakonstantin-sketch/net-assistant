package com.netassistant.app;

import android.content.Intent;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.View;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.progressindicator.LinearProgressIndicator;
import com.google.android.material.textfield.TextInputEditText;

import org.json.JSONObject;

public class LoginActivity extends AppCompatActivity {
    private TextInputEditText serverInput;
    private TextInputEditText tokenInput;
    private MaterialButton loginButton;
    private LinearProgressIndicator progress;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_login);

        serverInput = findViewById(R.id.server_input);
        tokenInput = findViewById(R.id.token_input);
        loginButton = findViewById(R.id.login_button);
        progress = findViewById(R.id.login_progress);

        Prefs prefs = new Prefs(this);
        serverInput.setText(prefs.server());
        if (!prefs.token().isEmpty()) {
            tokenInput.setText(prefs.token());
            tokenInput.setSelection(prefs.token().length());
        }

        loginButton.setOnClickListener(v -> login());
    }

    private void login() {
        String server = serverInput.getText() != null ? serverInput.getText().toString().trim() : "";
        String token = tokenInput.getText() != null ? tokenInput.getText().toString().trim() : "";
        if (TextUtils.isEmpty(server) || TextUtils.isEmpty(token)) {
            Ui.toast(this, "Укажите адрес сервера и токен");
            return;
        }
        setLoading(true);
        new Prefs(this).setServer(server);
        new Prefs(this).setToken(token);

        Api.get(this, "/health", new Api.Cb() {
            @Override
            public void ok(JSONObject body) {
                setLoading(false);
                new Prefs(LoginActivity.this).setSession("");
                startActivity(new Intent(LoginActivity.this, MainActivity.class));
                finish();
            }

            @Override
            public void fail(String message) {
                setLoading(false);
                Ui.toast(LoginActivity.this, "Не удалось подключиться: " + message);
            }
        });
    }

    private void setLoading(boolean loading) {
        loginButton.setEnabled(!loading);
        progress.setVisibility(loading ? View.VISIBLE : View.GONE);
        if (loading) {
            loginButton.setIcon(ContextCompat.getDrawable(this, R.drawable.ic_chevron));
        }
    }
}
