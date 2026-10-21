package com.netassistant.app;

import android.content.Intent;
import android.os.Bundle;
import android.view.MenuItem;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.fragment.app.Fragment;
import androidx.fragment.app.FragmentTransaction;

import com.google.android.material.bottomnavigation.BottomNavigationView;

public class MainActivity extends AppCompatActivity {
    private BottomNavigationView bottomNav;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (!new Prefs(this).loggedIn()) {
            startActivity(new Intent(this, LoginActivity.class));
            finish();
            return;
        }
        setContentView(R.layout.activity_main);

        bottomNav = findViewById(R.id.bottom_nav);
        bottomNav.setOnItemSelectedListener(item -> {
            int id = item.getItemId();
            if (id == R.id.nav_chat) {
                show(new ChatFragment());
                return true;
            }
            if (id == R.id.nav_commands) {
                show(new CommandsFragment());
                return true;
            }
            if (id == R.id.nav_devices) {
                show(new DevicesFragment());
                return true;
            }
            if (id == R.id.nav_audit) {
                show(new ActivityFragment());
                return true;
            }
            if (id == R.id.nav_more) {
                show(new MoreFragment());
                return true;
            }
            return false;
        });
        if (savedInstanceState == null) {
            show(new ChatFragment());
            bottomNav.setSelectedItemId(R.id.nav_chat);
        }
    }

    private void show(@NonNull Fragment fragment) {
        FragmentTransaction tx = getSupportFragmentManager().beginTransaction();
        tx.replace(R.id.container, fragment);
        tx.commitAllowingStateLoss();
    }

    public void showFiles() {
        startActivity(new Intent(this, FilesActivity.class));
    }
}
