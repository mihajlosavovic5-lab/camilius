import { defineRailway, github, postgres, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const data = volume("data", {
    sizeMB: 500,
    region: "sfo",
  });

  const db = postgres("postgres");

  // The web dashboard and the email-polling worker still share one service (simpler
  // for now) - the volume stays attached until it's confirmed unused, then it can be removed.
  const app = service("app", {
    source: github("mihajlosavovic5-lab/ai-auto-reply", { branch: "master" }),
    start: "python process_emails.py & exec gunicorn app:app --timeout 300 --worker-class gthread --threads 4",
    volumeMounts: {
      "/data": data,
    },
    env: {
      DB_PATH: "/data/conversations.db",
      DATABASE_URL: db.env.DATABASE_URL,
      FLASK_DEBUG: "false",
      ALERT_EMAIL: "mihajlosavovic75757@gmail.com",
      BACKUP_DIR: "/data/backups",
      ENCRYPTION_KEY: preserve(),
      FLASK_SECRET_KEY: preserve(),
      OPENAI_API_KEY: preserve(),
      RESEND_API_KEY: preserve(),
      // Paddle (sandbox for now; switching to production means changing PADDLE_ENV
      // and all the keys to real production values). Four prices now instead of one
      // PADDLE_PRICE_ID - Starter/Pro x Monthly/Yearly, each with its own 14-day,
      // card-required trial configured on the Price object itself in Paddle - see
      // paddle_billing.py.
      PADDLE_ENV: "sandbox",
      PADDLE_PRICE_STARTER_MONTHLY: "pri_01ky9jqaz42q9rckqmy51n9vzj",
      PADDLE_PRICE_STARTER_YEARLY: "pri_01ky9jx786m905np8aafjpx7je",
      PADDLE_PRICE_PRO_MONTHLY: "pri_01ky9jscjhmc2mm073fbjfkpjr",
      PADDLE_PRICE_PRO_YEARLY: "pri_01ky9jwbhs6gnqx4jt8t4g2esz",
      PADDLE_CLIENT_TOKEN: preserve(),
      PADDLE_WEBHOOK_SECRET: preserve(),
      // Cloudinary (storage.py) - object storage/CDN for business-uploaded website
      // media (logo/gallery/hero video, see business_media.py). Deliberately NOT the
      // /data volume above: the app service already runs a plain `gunicorn app:app`
      // process (no multi-instance/replica setup here), but disk written outside that
      // volume still doesn't survive a redeploy, and uploaded files need to be served
      // by URL from day one anyway - a CDN was the right call regardless of the volume.
      // preserve()d: set once directly in Railway from Cloudinary dashboard > Account
      // Details > "API Environment variable".
      CLOUDINARY_URL: preserve(),
      // "Continue with Google" (google_oauth.py) - inert (button hidden, /auth/google
      // 404s) until both are set. preserve()d: set once directly in Railway from Google
      // Cloud Console > APIs & Services > Credentials > OAuth 2.0 Client ID (type "Web
      // application", authorized redirect URI https://app.ai-auto-reply-mail.com/auth/google/callback).
      GOOGLE_CLIENT_ID: preserve(),
      GOOGLE_CLIENT_SECRET: preserve(),
      // Firebase Cloud Messaging (push_notifications.py) - owner appointment reminders
      // and new-message alerts push to the Android app through these. Both preserve()d:
      // set once directly in Railway from the Firebase console (Project settings ->
      // General for the project ID; Project settings -> Service accounts -> "Generate
      // new private key" for the JSON, pasted here as one line). Inert (silently skips
      // sending) until both are actually set - see push_notifications.py's docstring.
      FIREBASE_PROJECT_ID: preserve(),
      FIREBASE_SERVICE_ACCOUNT_JSON: preserve(),
    },
  });

  return project("ai-auto-reply", {
    resources: [app, db],
  });
});
