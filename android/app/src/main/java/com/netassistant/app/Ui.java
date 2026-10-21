package com.netassistant.app;

import android.content.Context;
import android.graphics.drawable.GradientDrawable;
import android.graphics.Typeface;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import com.google.android.material.card.MaterialCardView;

public final class Ui {
    private Ui() {
    }

    public static int dp(Context c, int dp) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, dp, c.getResources().getDisplayMetrics());
    }

    public static GradientDrawable gradient(int c1, int c2, int radiusDp, Context c) {
        GradientDrawable d = new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, new int[]{c1, c2});
        d.setCornerRadius(dp(c, radiusDp));
        return d;
    }

    public static GradientDrawable solid(int color, int radiusDp, Context c) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(c, radiusDp));
        return d;
    }

    public static TextView text(Context c, String text, int color, float sp, boolean bold, int gravity) {
        TextView t = new TextView(c);
        t.setText(text);
        t.setTextColor(color);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTypeface(bold ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        t.setGravity(gravity);
        return t;
    }

    public static void toast(Context c, String message) {
        Toast toast = Toast.makeText(c.getApplicationContext(), message, Toast.LENGTH_LONG);
        toast.setGravity(Gravity.CENTER, 0, 0);
        View view = toast.getView();
        if (view != null) {
            view.setBackground(ContextCompat.getDrawable(c, R.drawable.bg_glass));
            view.setPadding(dp(c, 16), dp(c, 12), dp(c, 16), dp(c, 12));
            view.setElevation(8f);
        }
        TextView tv = view.findViewById(android.R.id.message);
        if (tv != null) {
            tv.setTextColor(0xFFFFFFFF);
            tv.setTextSize(15f);
            tv.setGravity(Gravity.CENTER);
            tv.setTypeface(Typeface.DEFAULT_BOLD);
        }
        toast.show();
    }

    public static MaterialCardView card(Context c, int c1, int c2, int radiusDp, float elevation) {
        MaterialCardView card = new MaterialCardView(c);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(c, 8), dp(c, 6), dp(c, 8), dp(c, 6));
        card.setLayoutParams(lp);
        card.setRadius(dp(c, radiusDp));
        card.setCardBackgroundColor(0xFFFFFFFF);
        card.setCardElevation(elevation);
        card.setStrokeWidth(dp(c, 1));
        card.setStrokeColor(ContextCompat.getColor(c, R.color.card_stroke));
        return card;
    }

    public static int color(Context c, int resId) {
        return ContextCompat.getColor(c, resId);
    }
}
