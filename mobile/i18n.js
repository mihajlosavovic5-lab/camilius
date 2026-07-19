// Camilius AI — lightweight translation layer (vanilla JS, no framework, no build step,
// same philosophy as app.js). Loaded BEFORE app.js in index.html so its functions exist
// in time, but nothing here calls storageGet/storageSet at the top level - those are
// defined in app.js and are only actually called later, from initLanguage(), by which
// point app.js has already run (classic <script> tags share one global scope, so a
// function defined in a later script is still reachable from here at call time).

const LANGUAGES = [
    { code: "en", name: "English" },
    { code: "sr", name: "Srpski" },
    { code: "es", name: "Español" },
    { code: "pt", name: "Português" },
    { code: "fr", name: "Français" },
    { code: "de", name: "Deutsch" },
    { code: "it", name: "Italiano" },
    { code: "ru", name: "Русский" },
    { code: "zh", name: "中文" },
    { code: "tr", name: "Türkçe" },
];

const TRANSLATIONS = {
    en: {
        "onboarding.skip": "Skip",
        "onboarding.slide1.title": "Welcome to Camilius AI",
        "onboarding.slide1.body": "Your AI assistant for customer emails.",
        "onboarding.slide2.title": "Reads emails for you",
        "onboarding.slide2.body": "When a customer emails your business, Camilius reads it right away — day or night.",
        "onboarding.slide3.title": "Writes a reply for you",
        "onboarding.slide3.body": "The AI drafts a reply based on your working hours, price list, and FAQ.",
        "onboarding.slide4.title": "You approve and send",
        "onboarding.slide4.body": "Nothing goes out without your approval — review, edit if you like, then send.",
        "onboarding.getStarted": "Get started",

        "login.username": "Username",
        "login.password": "Password",
        "login.submit": "Log in",
        "login.submitLoading": "Logging in…",
        "login.error": "Wrong username or password.",
        "login.forgot": "Forgot password?",
        "login.signup": "Create accounttt",

        "lang.pickerTitle": "Choose language",

        "inbox.subtitle": "Customer questions",
        "inbox.processedToday": "Processed today",
        "inbox.inactiveBanner": "Subscription not active — new emails aren't being processed.",
        "inbox.searchPlaceholder": "Search conversations",
        "inbox.filterAll": "All",
        "inbox.filterPending": "Pending",
        "inbox.filterSent": "Sent",
        "inbox.emptyTitle": "You're all set — Camilius is watching your inbox",
        "inbox.emptyDetail": "New customer questions will show up here within 60 seconds.",
        "inbox.emptySearchTitle": "No matching conversations",
        "inbox.emptySearchDetail": "Try a different search term.",
        "inbox.errorTitle": "Couldn't load your inbox.",
        "inbox.errorGeneric": "Couldn't load your inbox. Try again.",
        "inbox.retry": "Try again",

        "detail.question": "Customer question",
        "detail.suggestedReply": "Suggested reply — edit before sending",
        "detail.send": "Send reply",
        "detail.sendLoading": "Sending…",
        "detail.sentReply": "Sent reply",
        "detail.badgePending": "Awaiting approval",
        "detail.badgeSent": "Sent",
        "detail.error": "Couldn't send the reply. Try again.",
        "detail.back": "Back",

        "settings.title": "Settings",
        "settings.name": "Business name",
        "settings.email": "Email (read-only)",
        "settings.hours": "Working hours",
        "settings.prices": "Price list",
        "settings.faq": "FAQ",
        "settings.appPassword": "Gmail app password (leave blank to keep current)",
        "settings.save": "Save changes",
        "settings.saveLoading": "Saving…",
        "settings.saved": "Your details have been saved.",
        "settings.error": "Couldn't save changes.",
        "settings.logout": "Log out",

        "tab.inbox": "Inbox",
        "tab.settings": "Settings",

        "offline": "No internet connection",
        "update.available": "A new version of the app is available.",
        "update.download": "Download",
        "update.dismiss": "Dismiss",
        "update.ready": "An app update is ready.",
        "update.install": "Install",

        "network.error": "No connection. Check your internet and try again.",
    },
    sr: {
        "onboarding.skip": "Preskoči",
        "onboarding.slide1.title": "Dobrodošli u Camilius AI",
        "onboarding.slide1.body": "Vaš AI asistent za mejlove kupaca.",
        "onboarding.slide2.title": "Čita mejlove umesto vas",
        "onboarding.slide2.body": "Kada kupac pošalje mejl vašoj firmi, Camilius ga odmah pročita — danju ili noću.",
        "onboarding.slide3.title": "Piše odgovor umesto vas",
        "onboarding.slide3.body": "AI piše odgovor na osnovu vašeg radnog vremena, cenovnika i čestih pitanja.",
        "onboarding.slide4.title": "Vi odobravate i šaljete",
        "onboarding.slide4.body": "Ništa se ne šalje bez vašeg odobrenja — pregledajte, izmenite ako želite, pa pošaljite.",
        "onboarding.getStarted": "Počnimo",

        "login.username": "Korisničko ime",
        "login.password": "Lozinka",
        "login.submit": "Prijavi se",
        "login.submitLoading": "Prijavljivanje…",
        "login.error": "Pogrešno korisničko ime ili lozinka.",
        "login.forgot": "Zaboravili ste lozinku?",
        "login.signup": "Napravi nalog",

        "lang.pickerTitle": "Izaberite jezik",

        "inbox.subtitle": "Pitanja kupaca",
        "inbox.processedToday": "Obrađeno danas",
        "inbox.inactiveBanner": "Pretplata nije aktivna — novi mejlovi se ne obrađuju.",
        "inbox.searchPlaceholder": "Pretraži razgovore",
        "inbox.filterAll": "Sve",
        "inbox.filterPending": "Na čekanju",
        "inbox.filterSent": "Poslato",
        "inbox.emptyTitle": "Sve je spremno — Camilius nadgleda vaš inboks",
        "inbox.emptyDetail": "Nova pitanja kupaca pojaviće se ovde u roku od 60 sekundi.",
        "inbox.emptySearchTitle": "Nema podudarajućih razgovora",
        "inbox.emptySearchDetail": "Probajte drugi pojam za pretragu.",
        "inbox.errorTitle": "Nije moguće učitati inboks.",
        "inbox.errorGeneric": "Nije moguće učitati inboks. Pokušajte ponovo.",
        "inbox.retry": "Pokušaj ponovo",

        "detail.question": "Pitanje kupca",
        "detail.suggestedReply": "Predloženi odgovor — izmenite pre slanja",
        "detail.send": "Pošalji odgovor",
        "detail.sendLoading": "Slanje…",
        "detail.sentReply": "Poslati odgovor",
        "detail.badgePending": "Čeka odobrenje",
        "detail.badgeSent": "Poslato",
        "detail.error": "Slanje odgovora nije uspelo. Pokušajte ponovo.",
        "detail.back": "Nazad",

        "settings.title": "Podešavanja",
        "settings.name": "Naziv firme",
        "settings.email": "Email (samo za čitanje)",
        "settings.hours": "Radno vreme",
        "settings.prices": "Cenovnik",
        "settings.faq": "Česta pitanja",
        "settings.appPassword": "Gmail lozinka za aplikacije (ostavite prazno da zadržite trenutnu)",
        "settings.save": "Sačuvaj izmene",
        "settings.saveLoading": "Čuvanje…",
        "settings.saved": "Vaši podaci su sačuvani.",
        "settings.error": "Čuvanje izmena nije uspelo.",
        "settings.logout": "Odjavi se",

        "tab.inbox": "Inboks",
        "tab.settings": "Podešavanja",

        "offline": "Nema internet konekcije",
        "update.available": "Dostupna je nova verzija aplikacije.",
        "update.download": "Preuzmi",
        "update.dismiss": "Odbaci",
        "update.ready": "Ažuriranje aplikacije je spremno.",
        "update.install": "Instaliraj",

        "network.error": "Nema konekcije. Proverite internet i pokušajte ponovo.",
    },
    es: {
        "onboarding.skip": "Omitir",
        "onboarding.slide1.title": "Bienvenido a Camilius AI",
        "onboarding.slide1.body": "Tu asistente de IA para los correos de tus clientes.",
        "onboarding.slide2.title": "Lee los correos por ti",
        "onboarding.slide2.body": "Cuando un cliente le escribe a tu negocio, Camilius lo lee al instante — de día o de noche.",
        "onboarding.slide3.title": "Redacta una respuesta por ti",
        "onboarding.slide3.body": "La IA redacta una respuesta según tu horario, tu lista de precios y tus preguntas frecuentes.",
        "onboarding.slide4.title": "Tú apruebas y envías",
        "onboarding.slide4.body": "Nada se envía sin tu aprobación — revisa, edita si quieres, y luego envía.",
        "onboarding.getStarted": "Empezar",

        "login.username": "Usuario",
        "login.password": "Contraseña",
        "login.submit": "Iniciar sesión",
        "login.submitLoading": "Iniciando sesión…",
        "login.error": "Usuario o contraseña incorrectos.",
        "login.forgot": "¿Olvidaste tu contraseña?",
        "login.signup": "Crear cuenta",

        "lang.pickerTitle": "Elige un idioma",

        "inbox.subtitle": "Preguntas de clientes",
        "inbox.processedToday": "Procesado hoy",
        "inbox.inactiveBanner": "La suscripción no está activa — los nuevos correos no se están procesando.",
        "inbox.searchPlaceholder": "Buscar conversaciones",
        "inbox.filterAll": "Todo",
        "inbox.filterPending": "Pendiente",
        "inbox.filterSent": "Enviado",
        "inbox.emptyTitle": "Todo listo — Camilius está vigilando tu bandeja de entrada",
        "inbox.emptyDetail": "Las nuevas preguntas de clientes aparecerán aquí en menos de 60 segundos.",
        "inbox.emptySearchTitle": "No hay conversaciones que coincidan",
        "inbox.emptySearchDetail": "Prueba con otro término de búsqueda.",
        "inbox.errorTitle": "No se pudo cargar tu bandeja de entrada.",
        "inbox.errorGeneric": "No se pudo cargar tu bandeja de entrada. Inténtalo de nuevo.",
        "inbox.retry": "Intentar de nuevo",

        "detail.question": "Pregunta del cliente",
        "detail.suggestedReply": "Respuesta sugerida — edítala antes de enviar",
        "detail.send": "Enviar respuesta",
        "detail.sendLoading": "Enviando…",
        "detail.sentReply": "Respuesta enviada",
        "detail.badgePending": "Esperando aprobación",
        "detail.badgeSent": "Enviado",
        "detail.error": "No se pudo enviar la respuesta. Inténtalo de nuevo.",
        "detail.back": "Atrás",

        "settings.title": "Ajustes",
        "settings.name": "Nombre del negocio",
        "settings.email": "Correo (solo lectura)",
        "settings.hours": "Horario de atención",
        "settings.prices": "Lista de precios",
        "settings.faq": "Preguntas frecuentes",
        "settings.appPassword": "Contraseña de aplicación de Gmail (déjalo en blanco para mantener la actual)",
        "settings.save": "Guardar cambios",
        "settings.saveLoading": "Guardando…",
        "settings.saved": "Tus datos se han guardado.",
        "settings.error": "No se pudieron guardar los cambios.",
        "settings.logout": "Cerrar sesión",

        "tab.inbox": "Bandeja",
        "tab.settings": "Ajustes",

        "offline": "Sin conexión a internet",
        "update.available": "Hay una nueva versión de la aplicación disponible.",
        "update.download": "Descargar",
        "update.dismiss": "Descartar",
        "update.ready": "Hay una actualización lista.",
        "update.install": "Instalar",

        "network.error": "Sin conexión. Comprueba tu internet e inténtalo de nuevo.",
    },
    pt: {
        "onboarding.skip": "Pular",
        "onboarding.slide1.title": "Bem-vindo ao Camilius AI",
        "onboarding.slide1.body": "Seu assistente de IA para os e-mails dos seus clientes.",
        "onboarding.slide2.title": "Lê os e-mails por você",
        "onboarding.slide2.body": "Quando um cliente envia um e-mail para sua empresa, o Camilius o lê na hora — de dia ou de noite.",
        "onboarding.slide3.title": "Escreve uma resposta por você",
        "onboarding.slide3.body": "A IA redige uma resposta com base no seu horário de funcionamento, tabela de preços e perguntas frequentes.",
        "onboarding.slide4.title": "Você aprova e envia",
        "onboarding.slide4.body": "Nada é enviado sem sua aprovação — revise, edite se quiser, e depois envie.",
        "onboarding.getStarted": "Começar",

        "login.username": "Usuário",
        "login.password": "Senha",
        "login.submit": "Entrar",
        "login.submitLoading": "Entrando…",
        "login.error": "Usuário ou senha incorretos.",
        "login.forgot": "Esqueceu a senha?",
        "login.signup": "Criar conta",

        "lang.pickerTitle": "Escolha o idioma",

        "inbox.subtitle": "Perguntas de clientes",
        "inbox.processedToday": "Processado hoje",
        "inbox.inactiveBanner": "A assinatura não está ativa — novos e-mails não estão sendo processados.",
        "inbox.searchPlaceholder": "Pesquisar conversas",
        "inbox.filterAll": "Todos",
        "inbox.filterPending": "Pendente",
        "inbox.filterSent": "Enviado",
        "inbox.emptyTitle": "Tudo pronto — o Camilius está de olho na sua caixa de entrada",
        "inbox.emptyDetail": "Novas perguntas de clientes vão aparecer aqui em até 60 segundos.",
        "inbox.emptySearchTitle": "Nenhuma conversa encontrada",
        "inbox.emptySearchDetail": "Tente outro termo de busca.",
        "inbox.errorTitle": "Não foi possível carregar sua caixa de entrada.",
        "inbox.errorGeneric": "Não foi possível carregar sua caixa de entrada. Tente novamente.",
        "inbox.retry": "Tentar novamente",

        "detail.question": "Pergunta do cliente",
        "detail.suggestedReply": "Resposta sugerida — edite antes de enviar",
        "detail.send": "Enviar resposta",
        "detail.sendLoading": "Enviando…",
        "detail.sentReply": "Resposta enviada",
        "detail.badgePending": "Aguardando aprovação",
        "detail.badgeSent": "Enviado",
        "detail.error": "Não foi possível enviar a resposta. Tente novamente.",
        "detail.back": "Voltar",

        "settings.title": "Configurações",
        "settings.name": "Nome da empresa",
        "settings.email": "E-mail (somente leitura)",
        "settings.hours": "Horário de funcionamento",
        "settings.prices": "Tabela de preços",
        "settings.faq": "Perguntas frequentes",
        "settings.appPassword": "Senha de aplicativo do Gmail (deixe em branco para manter a atual)",
        "settings.save": "Salvar alterações",
        "settings.saveLoading": "Salvando…",
        "settings.saved": "Seus dados foram salvos.",
        "settings.error": "Não foi possível salvar as alterações.",
        "settings.logout": "Sair",

        "tab.inbox": "Caixa de entrada",
        "tab.settings": "Configurações",

        "offline": "Sem conexão com a internet",
        "update.available": "Uma nova versão do aplicativo está disponível.",
        "update.download": "Baixar",
        "update.dismiss": "Dispensar",
        "update.ready": "Uma atualização está pronta.",
        "update.install": "Instalar",

        "network.error": "Sem conexão. Verifique sua internet e tente novamente.",
    },
    fr: {
        "onboarding.skip": "Passer",
        "onboarding.slide1.title": "Bienvenue sur Camilius AI",
        "onboarding.slide1.body": "Votre assistant IA pour les e-mails de vos clients.",
        "onboarding.slide2.title": "Lit les e-mails à votre place",
        "onboarding.slide2.body": "Quand un client écrit à votre entreprise, Camilius le lit aussitôt — de jour comme de nuit.",
        "onboarding.slide3.title": "Rédige une réponse à votre place",
        "onboarding.slide3.body": "L'IA rédige une réponse selon vos horaires, votre grille tarifaire et votre FAQ.",
        "onboarding.slide4.title": "Vous approuvez et envoyez",
        "onboarding.slide4.body": "Rien n'est envoyé sans votre accord — relisez, modifiez si besoin, puis envoyez.",
        "onboarding.getStarted": "Commencer",

        "login.username": "Nom d'utilisateur",
        "login.password": "Mot de passe",
        "login.submit": "Se connecter",
        "login.submitLoading": "Connexion…",
        "login.error": "Nom d'utilisateur ou mot de passe incorrect.",
        "login.forgot": "Mot de passe oublié ?",
        "login.signup": "Créer un compte",

        "lang.pickerTitle": "Choisissez une langue",

        "inbox.subtitle": "Questions des clients",
        "inbox.processedToday": "Traités aujourd'hui",
        "inbox.inactiveBanner": "L'abonnement n'est pas actif — les nouveaux e-mails ne sont pas traités.",
        "inbox.searchPlaceholder": "Rechercher des conversations",
        "inbox.filterAll": "Tout",
        "inbox.filterPending": "En attente",
        "inbox.filterSent": "Envoyé",
        "inbox.emptyTitle": "Tout est prêt — Camilius surveille votre boîte de réception",
        "inbox.emptyDetail": "Les nouvelles questions des clients apparaîtront ici sous 60 secondes.",
        "inbox.emptySearchTitle": "Aucune conversation correspondante",
        "inbox.emptySearchDetail": "Essayez un autre terme de recherche.",
        "inbox.errorTitle": "Impossible de charger votre boîte de réception.",
        "inbox.errorGeneric": "Impossible de charger votre boîte de réception. Réessayez.",
        "inbox.retry": "Réessayer",

        "detail.question": "Question du client",
        "detail.suggestedReply": "Réponse suggérée — modifiez avant l'envoi",
        "detail.send": "Envoyer la réponse",
        "detail.sendLoading": "Envoi…",
        "detail.sentReply": "Réponse envoyée",
        "detail.badgePending": "En attente d'approbation",
        "detail.badgeSent": "Envoyé",
        "detail.error": "Impossible d'envoyer la réponse. Réessayez.",
        "detail.back": "Retour",

        "settings.title": "Paramètres",
        "settings.name": "Nom de l'entreprise",
        "settings.email": "E-mail (lecture seule)",
        "settings.hours": "Horaires d'ouverture",
        "settings.prices": "Grille tarifaire",
        "settings.faq": "Questions fréquentes",
        "settings.appPassword": "Mot de passe d'application Gmail (laissez vide pour garder l'actuel)",
        "settings.save": "Enregistrer les modifications",
        "settings.saveLoading": "Enregistrement…",
        "settings.saved": "Vos informations ont été enregistrées.",
        "settings.error": "Impossible d'enregistrer les modifications.",
        "settings.logout": "Se déconnecter",

        "tab.inbox": "Boîte de réception",
        "tab.settings": "Paramètres",

        "offline": "Pas de connexion internet",
        "update.available": "Une nouvelle version de l'application est disponible.",
        "update.download": "Télécharger",
        "update.dismiss": "Ignorer",
        "update.ready": "Une mise à jour est prête.",
        "update.install": "Installer",

        "network.error": "Pas de connexion. Vérifiez votre internet et réessayez.",
    },
    de: {
        "onboarding.skip": "Überspringen",
        "onboarding.slide1.title": "Willkommen bei Camilius AI",
        "onboarding.slide1.body": "Dein KI-Assistent für die E-Mails deiner Kunden.",
        "onboarding.slide2.title": "Liest E-Mails für dich",
        "onboarding.slide2.body": "Wenn ein Kunde deinem Unternehmen schreibt, liest Camilius die E-Mail sofort — Tag und Nacht.",
        "onboarding.slide3.title": "Schreibt eine Antwort für dich",
        "onboarding.slide3.body": "Die KI verfasst eine Antwort auf Basis deiner Öffnungszeiten, Preisliste und FAQ.",
        "onboarding.slide4.title": "Du genehmigst und sendest",
        "onboarding.slide4.body": "Nichts wird ohne deine Zustimmung verschickt — prüfen, bei Bedarf bearbeiten, dann senden.",
        "onboarding.getStarted": "Los geht's",

        "login.username": "Benutzername",
        "login.password": "Passwort",
        "login.submit": "Anmelden",
        "login.submitLoading": "Anmeldung…",
        "login.error": "Falscher Benutzername oder falsches Passwort.",
        "login.forgot": "Passwort vergessen?",
        "login.signup": "Konto erstellen",

        "lang.pickerTitle": "Sprache wählen",

        "inbox.subtitle": "Kundenanfragen",
        "inbox.processedToday": "Heute bearbeitet",
        "inbox.inactiveBanner": "Abo nicht aktiv — neue E-Mails werden nicht verarbeitet.",
        "inbox.searchPlaceholder": "Unterhaltungen durchsuchen",
        "inbox.filterAll": "Alle",
        "inbox.filterPending": "Ausstehend",
        "inbox.filterSent": "Gesendet",
        "inbox.emptyTitle": "Alles bereit — Camilius behält deinen Posteingang im Blick",
        "inbox.emptyDetail": "Neue Kundenanfragen erscheinen hier innerhalb von 60 Sekunden.",
        "inbox.emptySearchTitle": "Keine passenden Unterhaltungen",
        "inbox.emptySearchDetail": "Versuche einen anderen Suchbegriff.",
        "inbox.errorTitle": "Posteingang konnte nicht geladen werden.",
        "inbox.errorGeneric": "Posteingang konnte nicht geladen werden. Versuch's noch mal.",
        "inbox.retry": "Erneut versuchen",

        "detail.question": "Kundenfrage",
        "detail.suggestedReply": "Vorgeschlagene Antwort — vor dem Senden bearbeiten",
        "detail.send": "Antwort senden",
        "detail.sendLoading": "Wird gesendet…",
        "detail.sentReply": "Gesendete Antwort",
        "detail.badgePending": "Wartet auf Freigabe",
        "detail.badgeSent": "Gesendet",
        "detail.error": "Antwort konnte nicht gesendet werden. Versuch's noch mal.",
        "detail.back": "Zurück",

        "settings.title": "Einstellungen",
        "settings.name": "Firmenname",
        "settings.email": "E-Mail (schreibgeschützt)",
        "settings.hours": "Öffnungszeiten",
        "settings.prices": "Preisliste",
        "settings.faq": "Häufige Fragen",
        "settings.appPassword": "Gmail-App-Passwort (leer lassen, um das aktuelle zu behalten)",
        "settings.save": "Änderungen speichern",
        "settings.saveLoading": "Speichern…",
        "settings.saved": "Deine Angaben wurden gespeichert.",
        "settings.error": "Änderungen konnten nicht gespeichert werden.",
        "settings.logout": "Abmelden",

        "tab.inbox": "Posteingang",
        "tab.settings": "Einstellungen",

        "offline": "Keine Internetverbindung",
        "update.available": "Eine neue Version der App ist verfügbar.",
        "update.download": "Herunterladen",
        "update.dismiss": "Verwerfen",
        "update.ready": "Ein Update ist bereit.",
        "update.install": "Installieren",

        "network.error": "Keine Verbindung. Prüfe dein Internet und versuch's noch mal.",
    },
    it: {
        "onboarding.skip": "Salta",
        "onboarding.slide1.title": "Benvenuto su Camilius AI",
        "onboarding.slide1.body": "Il tuo assistente IA per le email dei clienti.",
        "onboarding.slide2.title": "Legge le email al posto tuo",
        "onboarding.slide2.body": "Quando un cliente scrive alla tua attività, Camilius la legge subito — giorno e notte.",
        "onboarding.slide3.title": "Scrive una risposta al posto tuo",
        "onboarding.slide3.body": "L'IA redige una risposta in base ai tuoi orari, al listino prezzi e alle domande frequenti.",
        "onboarding.slide4.title": "Tu approvi e invii",
        "onboarding.slide4.body": "Niente viene inviato senza la tua approvazione — controlla, modifica se vuoi, poi invia.",
        "onboarding.getStarted": "Inizia",

        "login.username": "Nome utente",
        "login.password": "Password",
        "login.submit": "Accedi",
        "login.submitLoading": "Accesso in corso…",
        "login.error": "Nome utente o password errati.",
        "login.forgot": "Password dimenticata?",
        "login.signup": "Crea account",

        "lang.pickerTitle": "Scegli la lingua",

        "inbox.subtitle": "Domande dei clienti",
        "inbox.processedToday": "Elaborate oggi",
        "inbox.inactiveBanner": "Abbonamento non attivo — le nuove email non vengono elaborate.",
        "inbox.searchPlaceholder": "Cerca conversazioni",
        "inbox.filterAll": "Tutte",
        "inbox.filterPending": "In attesa",
        "inbox.filterSent": "Inviate",
        "inbox.emptyTitle": "Tutto pronto — Camilius sta monitorando la tua casella",
        "inbox.emptyDetail": "Le nuove domande dei clienti appariranno qui entro 60 secondi.",
        "inbox.emptySearchTitle": "Nessuna conversazione corrispondente",
        "inbox.emptySearchDetail": "Prova un altro termine di ricerca.",
        "inbox.errorTitle": "Impossibile caricare la casella di posta.",
        "inbox.errorGeneric": "Impossibile caricare la casella di posta. Riprova.",
        "inbox.retry": "Riprova",

        "detail.question": "Domanda del cliente",
        "detail.suggestedReply": "Risposta suggerita — modifica prima di inviare",
        "detail.send": "Invia risposta",
        "detail.sendLoading": "Invio in corso…",
        "detail.sentReply": "Risposta inviata",
        "detail.badgePending": "In attesa di approvazione",
        "detail.badgeSent": "Inviata",
        "detail.error": "Impossibile inviare la risposta. Riprova.",
        "detail.back": "Indietro",

        "settings.title": "Impostazioni",
        "settings.name": "Nome dell'attività",
        "settings.email": "Email (sola lettura)",
        "settings.hours": "Orari di apertura",
        "settings.prices": "Listino prezzi",
        "settings.faq": "Domande frequenti",
        "settings.appPassword": "Password per app Gmail (lascia vuoto per mantenere quella attuale)",
        "settings.save": "Salva modifiche",
        "settings.saveLoading": "Salvataggio…",
        "settings.saved": "I tuoi dati sono stati salvati.",
        "settings.error": "Impossibile salvare le modifiche.",
        "settings.logout": "Esci",

        "tab.inbox": "Posta",
        "tab.settings": "Impostazioni",

        "offline": "Nessuna connessione internet",
        "update.available": "È disponibile una nuova versione dell'app.",
        "update.download": "Scarica",
        "update.dismiss": "Ignora",
        "update.ready": "Un aggiornamento è pronto.",
        "update.install": "Installa",

        "network.error": "Nessuna connessione. Controlla la tua rete e riprova.",
    },
    ru: {
        "onboarding.skip": "Пропустить",
        "onboarding.slide1.title": "Добро пожаловать в Camilius AI",
        "onboarding.slide1.body": "Ваш ИИ-помощник для писем от клиентов.",
        "onboarding.slide2.title": "Читает письма за вас",
        "onboarding.slide2.body": "Когда клиент пишет вашей компании, Camilius сразу же читает письмо — днём и ночью.",
        "onboarding.slide3.title": "Пишет ответ за вас",
        "onboarding.slide3.body": "ИИ составляет ответ на основе вашего графика работы, прайс-листа и частых вопросов.",
        "onboarding.slide4.title": "Вы одобряете и отправляете",
        "onboarding.slide4.body": "Ничего не отправляется без вашего одобрения — просмотрите, отредактируйте при желании, затем отправьте.",
        "onboarding.getStarted": "Начать",

        "login.username": "Имя пользователя",
        "login.password": "Пароль",
        "login.submit": "Войти",
        "login.submitLoading": "Вход…",
        "login.error": "Неверное имя пользователя или пароль.",
        "login.forgot": "Забыли пароль?",
        "login.signup": "Создать аккаунт",

        "lang.pickerTitle": "Выберите язык",

        "inbox.subtitle": "Вопросы клиентов",
        "inbox.processedToday": "Обработано сегодня",
        "inbox.inactiveBanner": "Подписка не активна — новые письма не обрабатываются.",
        "inbox.searchPlaceholder": "Поиск по переписке",
        "inbox.filterAll": "Все",
        "inbox.filterPending": "В ожидании",
        "inbox.filterSent": "Отправлено",
        "inbox.emptyTitle": "Всё готово — Camilius следит за вашими письмами",
        "inbox.emptyDetail": "Новые вопросы клиентов появятся здесь в течение 60 секунд.",
        "inbox.emptySearchTitle": "Совпадений не найдено",
        "inbox.emptySearchDetail": "Попробуйте другой запрос.",
        "inbox.errorTitle": "Не удалось загрузить входящие.",
        "inbox.errorGeneric": "Не удалось загрузить входящие. Попробуйте снова.",
        "inbox.retry": "Повторить",

        "detail.question": "Вопрос клиента",
        "detail.suggestedReply": "Предложенный ответ — отредактируйте перед отправкой",
        "detail.send": "Отправить ответ",
        "detail.sendLoading": "Отправка…",
        "detail.sentReply": "Отправленный ответ",
        "detail.badgePending": "Ожидает одобрения",
        "detail.badgeSent": "Отправлено",
        "detail.error": "Не удалось отправить ответ. Попробуйте снова.",
        "detail.back": "Назад",

        "settings.title": "Настройки",
        "settings.name": "Название компании",
        "settings.email": "Email (только чтение)",
        "settings.hours": "Часы работы",
        "settings.prices": "Прайс-лист",
        "settings.faq": "Частые вопросы",
        "settings.appPassword": "Пароль приложения Gmail (оставьте пустым, чтобы сохранить текущий)",
        "settings.save": "Сохранить изменения",
        "settings.saveLoading": "Сохранение…",
        "settings.saved": "Ваши данные сохранены.",
        "settings.error": "Не удалось сохранить изменения.",
        "settings.logout": "Выйти",

        "tab.inbox": "Входящие",
        "tab.settings": "Настройки",

        "offline": "Нет подключения к интернету",
        "update.available": "Доступна новая версия приложения.",
        "update.download": "Скачать",
        "update.dismiss": "Скрыть",
        "update.ready": "Обновление готово.",
        "update.install": "Установить",

        "network.error": "Нет соединения. Проверьте интернет и попробуйте снова.",
    },
    zh: {
        "onboarding.skip": "跳过",
        "onboarding.slide1.title": "欢迎使用 Camilius AI",
        "onboarding.slide1.body": "您的客户邮件人工智能助手。",
        "onboarding.slide2.title": "自动帮您阅读邮件",
        "onboarding.slide2.body": "客户给您的企业发邮件时，Camilius 会立即读取——不分白天黑夜。",
        "onboarding.slide3.title": "自动帮您写回复",
        "onboarding.slide3.body": "AI 会根据您的营业时间、价目表和常见问题起草回复。",
        "onboarding.slide4.title": "您来审核并发送",
        "onboarding.slide4.body": "未经您批准，任何内容都不会发出——先查看，需要可编辑，然后发送。",
        "onboarding.getStarted": "开始使用",

        "login.username": "用户名",
        "login.password": "密码",
        "login.submit": "登录",
        "login.submitLoading": "正在登录…",
        "login.error": "用户名或密码错误。",
        "login.forgot": "忘记密码？",
        "login.signup": "创建账户",

        "lang.pickerTitle": "选择语言",

        "inbox.subtitle": "客户提问",
        "inbox.processedToday": "今日已处理",
        "inbox.inactiveBanner": "订阅未激活——新邮件将不会被处理。",
        "inbox.searchPlaceholder": "搜索对话",
        "inbox.filterAll": "全部",
        "inbox.filterPending": "待处理",
        "inbox.filterSent": "已发送",
        "inbox.emptyTitle": "一切就绪——Camilius 正在为您监控收件箱",
        "inbox.emptyDetail": "新的客户提问将在 60 秒内显示在这里。",
        "inbox.emptySearchTitle": "没有匹配的对话",
        "inbox.emptySearchDetail": "请尝试其他搜索词。",
        "inbox.errorTitle": "无法加载收件箱。",
        "inbox.errorGeneric": "无法加载收件箱，请重试。",
        "inbox.retry": "重试",

        "detail.question": "客户提问",
        "detail.suggestedReply": "建议回复 — 发送前可编辑",
        "detail.send": "发送回复",
        "detail.sendLoading": "发送中…",
        "detail.sentReply": "已发送的回复",
        "detail.badgePending": "等待批准",
        "detail.badgeSent": "已发送",
        "detail.error": "回复发送失败，请重试。",
        "detail.back": "返回",

        "settings.title": "设置",
        "settings.name": "企业名称",
        "settings.email": "邮箱（只读）",
        "settings.hours": "营业时间",
        "settings.prices": "价目表",
        "settings.faq": "常见问题",
        "settings.appPassword": "Gmail 应用专用密码（留空则保留当前密码）",
        "settings.save": "保存更改",
        "settings.saveLoading": "正在保存…",
        "settings.saved": "您的信息已保存。",
        "settings.error": "保存更改失败。",
        "settings.logout": "退出登录",

        "tab.inbox": "收件箱",
        "tab.settings": "设置",

        "offline": "无网络连接",
        "update.available": "有可用的应用新版本。",
        "update.download": "下载",
        "update.dismiss": "关闭",
        "update.ready": "应用更新已就绪。",
        "update.install": "安装",

        "network.error": "无连接。请检查网络后重试。",
    },
    tr: {
        "onboarding.skip": "Geç",
        "onboarding.slide1.title": "Camilius AI'a Hoş Geldiniz",
        "onboarding.slide1.body": "Müşteri e-postalarınız için yapay zeka asistanınız.",
        "onboarding.slide2.title": "E-postaları sizin için okur",
        "onboarding.slide2.body": "Bir müşteri işletmenize e-posta gönderdiğinde, Camilius onu hemen okur — gece gündüz.",
        "onboarding.slide3.title": "Sizin için bir yanıt yazar",
        "onboarding.slide3.body": "Yapay zeka, çalışma saatlerinize, fiyat listenize ve sık sorulan sorularınıza göre bir yanıt hazırlar.",
        "onboarding.slide4.title": "Siz onaylar ve gönderirsiniz",
        "onboarding.slide4.body": "Onayınız olmadan hiçbir şey gönderilmez — inceleyin, isterseniz düzenleyin, sonra gönderin.",
        "onboarding.getStarted": "Başla",

        "login.username": "Kullanıcı adı",
        "login.password": "Şifre",
        "login.submit": "Giriş yap",
        "login.submitLoading": "Giriş yapılıyor…",
        "login.error": "Kullanıcı adı veya şifre hatalı.",
        "login.forgot": "Şifrenizi mi unuttunuz?",
        "login.signup": "Hesap oluştur",

        "lang.pickerTitle": "Dil seçin",

        "inbox.subtitle": "Müşteri soruları",
        "inbox.processedToday": "Bugün işlenen",
        "inbox.inactiveBanner": "Abonelik aktif değil — yeni e-postalar işlenmiyor.",
        "inbox.searchPlaceholder": "Konuşmalarda ara",
        "inbox.filterAll": "Tümü",
        "inbox.filterPending": "Bekleyen",
        "inbox.filterSent": "Gönderildi",
        "inbox.emptyTitle": "Her şey hazır — Camilius gelen kutunuzu izliyor",
        "inbox.emptyDetail": "Yeni müşteri soruları 60 saniye içinde burada görünecek.",
        "inbox.emptySearchTitle": "Eşleşen konuşma yok",
        "inbox.emptySearchDetail": "Başka bir arama terimi deneyin.",
        "inbox.errorTitle": "Gelen kutunuz yüklenemedi.",
        "inbox.errorGeneric": "Gelen kutunuz yüklenemedi. Tekrar deneyin.",
        "inbox.retry": "Tekrar dene",

        "detail.question": "Müşteri sorusu",
        "detail.suggestedReply": "Önerilen yanıt — göndermeden önce düzenleyin",
        "detail.send": "Yanıtı gönder",
        "detail.sendLoading": "Gönderiliyor…",
        "detail.sentReply": "Gönderilen yanıt",
        "detail.badgePending": "Onay bekliyor",
        "detail.badgeSent": "Gönderildi",
        "detail.error": "Yanıt gönderilemedi. Tekrar deneyin.",
        "detail.back": "Geri",

        "settings.title": "Ayarlar",
        "settings.name": "İşletme adı",
        "settings.email": "E-posta (salt okunur)",
        "settings.hours": "Çalışma saatleri",
        "settings.prices": "Fiyat listesi",
        "settings.faq": "Sık sorulan sorular",
        "settings.appPassword": "Gmail uygulama şifresi (mevcut şifreyi korumak için boş bırakın)",
        "settings.save": "Değişiklikleri kaydet",
        "settings.saveLoading": "Kaydediliyor…",
        "settings.saved": "Bilgileriniz kaydedildi.",
        "settings.error": "Değişiklikler kaydedilemedi.",
        "settings.logout": "Çıkış yap",

        "tab.inbox": "Gelen kutusu",
        "tab.settings": "Ayarlar",

        "offline": "İnternet bağlantısı yok",
        "update.available": "Uygulamanın yeni bir sürümü mevcut.",
        "update.download": "İndir",
        "update.dismiss": "Kapat",
        "update.ready": "Bir güncelleme hazır.",
        "update.install": "Yükle",

        "network.error": "Bağlantı yok. İnternetinizi kontrol edip tekrar deneyin.",
    },
};

let currentLang = "en";

function t(key) {
    const dict = TRANSLATIONS[currentLang] || TRANSLATIONS.en;
    return dict[key] || TRANSLATIONS.en[key] || key;
}

function applyTranslations() {
    document.documentElement.lang = currentLang;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
        node.textContent = t(node.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
        node.placeholder = t(node.getAttribute("data-i18n-placeholder"));
    });
    const label = document.getElementById("lang-select-label");
    if (label) {
        const lang = LANGUAGES.find((l) => l.code === currentLang);
        label.textContent = lang ? lang.name : currentLang;
    }
}

async function setLanguage(code) {
    if (!TRANSLATIONS[code]) return;
    currentLang = code;
    await storageSet("language", code);
    applyTranslations();
}

async function initLanguage() {
    const saved = await storageGet("language");
    if (saved && TRANSLATIONS[saved]) {
        currentLang = saved;
    } else {
        const deviceLang = (navigator.language || "en").slice(0, 2).toLowerCase();
        currentLang = TRANSLATIONS[deviceLang] ? deviceLang : "en";
    }
    applyTranslations();
}

function setupLanguagePicker() {
    const btn = document.getElementById("lang-select-btn");
    const modal = document.getElementById("lang-modal");
    const backdrop = document.getElementById("lang-modal-backdrop");
    const list = document.getElementById("lang-modal-list");
    if (!btn || !modal || !list) return;

    const openPicker = () => {
        modal.classList.add("open");
        btn.classList.add("active");
    };
    const closePicker = () => {
        modal.classList.remove("open");
        btn.classList.remove("active");
    };

    LANGUAGES.forEach((lang) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "lang-option-btn";
        row.textContent = lang.name;
        row.addEventListener("click", async () => {
            await setLanguage(lang.code);
            closePicker();
        });
        list.appendChild(row);
    });

    btn.addEventListener("click", () => {
        if (modal.classList.contains("open")) closePicker();
        else openPicker();
    });
    backdrop.addEventListener("click", closePicker);
}
