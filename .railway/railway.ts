import { defineRailway, github, postgres, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const data = volume("data", {
    sizeMB: 500,
    region: "sfo",
  });

  const db = postgres("postgres");

  // Web dashboard i email-polling worker i dalje dele jedan servis (jednostavnije
  // za sad) - volume ostaje prikacen dok se ne potvrdi da Postgres migracija radi,
  // pa se moze ukloniti.
  const app = service("app", {
    source: github("mihajlosavovic5-lab/ai-auto-reply", { branch: "master" }),
    start: "python obradi_email.py & exec gunicorn app:app",
    volumeMounts: {
      "/data": data,
    },
    env: {
      DB_PATH: "/data/razgovori.db",
      DATABASE_URL: db.env.DATABASE_URL,
      FLASK_DEBUG: "false",
      ALERT_EMAIL: "mihajlosavovic5@gmail.com",
      BACKUP_DIR: "/data/backups",
      ENCRYPTION_KEY: preserve(),
      FLASK_SECRET_KEY: preserve(),
      OPENAI_API_KEY: preserve(),
      RESEND_API_KEY: preserve(),
      // Paddle (sandbox za sad; pri prelasku na produkciju menja se PADDLE_ENV
      // i svi kljucevi u prave production vrednosti)
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
