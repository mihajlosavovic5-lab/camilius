import { defineRailway, github, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const data = volume("data", {
    sizeMB: 500,
  });

  // Web dashboard i email-polling worker dele isti SQLite fajl, pa moraju
  // biti u istom servisu - Railway dozvoljava da se volume prikaci samo
  // na jedan servis. Worker radi u pozadini, gunicorn je glavni proces.
  const app = service("app", {
    source: github("mihajlosavovic5-lab/ai-auto-reply", { branch: "master" }),
    start: "python obradi_email.py & exec gunicorn app:app",
    volumeMounts: {
      "/data": data,
    },
    env: {
      DB_PATH: "/data/razgovori.db",
      FLASK_DEBUG: "false",
    },
  });

  return project("ai-auto-reply", {
    resources: [app],
  });
});
