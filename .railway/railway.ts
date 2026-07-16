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
    start: "python process_emails.py & exec gunicorn app:app",
    volumeMounts: {
      "/data": data,
    },
    env: {
      DB_PATH: "/data/conversations.db",
      DATABASE_URL: db.env.DATABASE_URL,
      FLASK_DEBUG: "false",
      ALERT_EMAIL: "mihajlosavovic5@gmail.com",
      BACKUP_DIR: "/data/backups",
      ENCRYPTION_KEY: preserve(),
      FLASK_SECRET_KEY: preserve(),
      OPENAI_API_KEY: preserve(),
      RESEND_API_KEY: preserve(),
      // Paddle (sandbox for now; switching to production means changing PADDLE_ENV
      // and all the keys to real production values)
      PADDLE_ENV: "sandbox",
      PADDLE_PRICE_ID: "pri_01kxkbxekp5274dy4zgg947ezg",
      PADDLE_CLIENT_TOKEN: preserve(),
      PADDLE_WEBHOOK_SECRET: preserve(),
    },
  });

  return project("ai-auto-reply", {
    resources: [app, db],
  });
});
