// Camilius — website translation layer (vanilla JS, no framework, no build step),
// mirrors mobile/i18n.js's approach exactly (same LANGUAGES list, same t()/
// applyTranslations() mechanism, same data-i18n/data-i18n-placeholder HTML attributes).
// Client-side only: server-rendered dynamic content (flash messages like "Wrong
// username or password.", {{ business.name }}, etc.) is NOT translated by this layer,
// same accepted limitation the mobile app already has.

// "flag" is a filename under /static/flags/ (from the flag-icons project, MIT
// licensed), not the language code. Emoji flags were tried first but Chrome on Windows
// renders them as plain "GB"/"ES" text instead of an actual flag glyph, so real image
// files are used instead for consistent cross-platform rendering. Most are the
// original SVGs, but Spain's and Serbia's coats of arms make their SVGs huge (80KB/
// 175KB) for an icon shown at ~20px, so those two are pre-rasterized to small PNGs.
const LANGUAGES = [
    { code: "en", name: "English", flag: "gb.svg" },
    { code: "es", name: "Español", flag: "es.png" },
    { code: "pt", name: "Português", flag: "pt.svg" },
    { code: "fr", name: "Français", flag: "fr.svg" },
    { code: "de", name: "Deutsch", flag: "de.svg" },
    { code: "it", name: "Italiano", flag: "it.svg" },
    { code: "ru", name: "Русский", flag: "ru.svg" },
    { code: "sr", name: "Srpski", flag: "rs.png" },
    { code: "zh", name: "中文", flag: "cn.svg" },
    { code: "tr", name: "Türkçe", flag: "tr.svg" },
];

const TRANSLATIONS = {
    en: {
        "common.conversations": "Conversations",
        "common.schedule": "Schedule",
        "common.subscription": "Subscription",
        "common.profile": "Profile",
        "common.settings": "Settings",
        "common.logOut": "Log out",
        "common.logIn": "Log in",
        "common.termsOfService": "Terms of Service",
        "common.privacyPolicy": "Privacy Policy",

        "login.tagline": "AI reads your customers' Instagram messages and drafts replies — you just review and send.",
        "login.title": "Log in",
        "login.subtitle": "Welcome back.",
        "login.usernameLabel": "Username, email, or phone number",
        "login.passwordLabel": "Password",
        "login.noAccount": "Don't have an account?",
        "login.registerLink": "Register your business",
        "login.forgotLink": "Forgot your password?",
        "login.replayIntro": "Replay intro",

        "register.tagline": "Register your business in a few minutes — AI starts drafting replies to customer Instagram messages right away.",
        "register.title": "Register your business",
        "register.subtitle": "You can change all of these fields later in settings. You'll connect your Instagram account in the next step.",
        "register.accountSection": "Account",
        "register.businessName": "Business name",
        "register.businessNamePlaceholder": "e.g. Frizerski salon Stil",
        "register.username": "Username",
        "register.usernameHint": "(for dashboard login)",
        "register.password": "Password",
        "register.contactEmail": "Contact email",
        "register.contactEmailHint": "(for account recovery only - not read by the AI)",
        "register.phone": "Phone number",
        "register.phoneHint": "(optional - lets you log in with it too)",
        "register.businessDetailsSection": "Business details — the AI uses this to write replies",
        "register.hours": "Working hours",
        "register.hoursPlaceholder": "Monday: 09:00 - 20:00\nSaturday: 09:00 - 16:00\nSunday: closed",
        "register.prices": "Price list",
        "register.pricesPlaceholder": "Men's haircut: 800 din\nWomen's haircut: 1500 din",
        "register.faq": "Tell us more about your business",
        "register.faqPlaceholder": "Q: Do I need to book an appointment?\nA: Yes, we recommend booking in advance.\n\nAnything else worth knowing about your business.",
        "register.faqHint": "The more you share, the more questions our AI can answer for your customers.",
        "register.acceptPrefix": "I accept the",
        "register.acceptAnd": "and",
        "register.submit": "Register",
        "register.alreadyHaveAccount": "Already have an account?",
        "register.usernameTaken": "That username is already taken.",
        "register.emailTaken": "That email is already registered.",
        "register.phoneTaken": "That phone number is already registered.",

        "forgot.title": "Forgot password",
        "forgot.subtitle": "Enter the email or username you registered with — we'll send a password reset link to the email on file.",
        "forgot.identifierLabel": "Email or username",
        "forgot.submit": "Send reset link",
        "forgot.findByPhone": "Find by mobile number",
        "forgot.phoneSubtitle": "Enter the phone number on your account — we'll send a password reset link to the email on file.",
        "forgot.phoneLabel": "Phone number",
        "forgot.phoneSubmit": "Find account",
        "forgot.backToIdentifier": "← Use email or username instead",
        "forgot.backToLogin": "← Back to login",

        "reset.invalidTitle": "This link is no longer valid",
        "reset.invalidBody": "This password reset link has expired or has already been used (valid for 1 hour after it's sent).",
        "reset.requestNewLink": "Request a new link",
        "reset.title": "Set a new password",
        "reset.newPasswordLabel": "New password",
        "reset.submit": "Save new password",

        // Landing page "See it in action" slider (Faza 6) - 10 example Instagram DM
        // conversations across different business types. Sender names are proper
        // nouns and deliberately NOT translated (same as {{ business.name }} elsewhere).
        "landing.slider.time.now": "Just now",
        "landing.slider.time.m2": "2m ago",
        "landing.slider.time.m5": "5m ago",
        "landing.slider.time.m12": "12m ago",
        "landing.slider.time.h1": "1h ago",

        "landing.slider.example1.question": "Hi! What are your Saturday hours, and how much is a women's haircut?",
        "landing.slider.example1.reply": "Hi! We're open Saturdays from 9 to 16.\nA women's haircut is €35.\nWant me to book you in for a time?",
        "landing.slider.example2.question": "Do you do teeth whitening, and can I just walk in or do I need an appointment?",
        "landing.slider.example2.reply": "Yes, we offer teeth whitening for €120. We recommend booking ahead so we can hold a slot for you — want me to check what's free this week?",
        "landing.slider.example3.question": "My iPhone 13 screen is cracked — how much to fix it, and how long does it take?",
        "landing.slider.example3.reply": "An iPhone 13 screen replacement is €89, usually ready the same day if you drop it off before 2pm. Want to bring it in today?",
        "landing.slider.example4.question": "My kitchen faucet is leaking — can someone come take a look this week?",
        "landing.slider.example4.reply": "Sorry to hear that! We have openings Wednesday and Thursday afternoon. A callout and inspection is €25, and we'll quote any parts before starting. Which day works better?",
        "landing.slider.example5.question": "What time is check-in, and are pets allowed?",
        "landing.slider.example5.reply": "Check-in is from 15:00, check-out by 11:00.\nYes, pets are welcome — just let us know in advance so we can prepare the space.",
        "landing.slider.example6.question": "How much for a one-hour strategy call, and how do I book?",
        "landing.slider.example6.reply": "A one-hour strategy call is €90. I can send you a booking link with my open slots for this week — want me to send it now?",
        "landing.slider.example7.question": "Does the green midi dress come in size M, and what's your return policy?",
        "landing.slider.example7.reply": "Yes, size M is in stock! Returns are accepted within 14 days with the tag still attached. Want me to hold one for you?",
        "landing.slider.example8.question": "How much is a gel manicure, and do I need to book in advance?",
        "landing.slider.example8.reply": "A gel manicure is €28. We recommend booking a day ahead, especially for weekends — want me to check tomorrow's availability?",
        "landing.slider.example9.question": "What's the price for a monthly plan, and is it OK if I've never trained before?",
        "landing.slider.example9.reply": "A monthly plan is €80 for 8 sessions.\nTotally fine if you're just starting out — the first session is a light intro so we can build a plan around you.",
        "landing.slider.example10.question": "Do you have availability for a wedding on September 12th, and what's included in your package?",
        "landing.slider.example10.reply": "September 12th is still open!\nThe standard package includes 6 hours of coverage and 300+ edited photos. Want me to send the full package details?",
    },

    es: {
        "landing.slider.time.now": "Ahora mismo",
        "landing.slider.time.m2": "Hace 2 min",
        "landing.slider.time.m5": "Hace 5 min",
        "landing.slider.time.m12": "Hace 12 min",
        "landing.slider.time.h1": "Hace 1 h",

        "landing.slider.example1.question": "¡Hola! ¿Cuál es su horario de sábado y cuánto cuesta un corte de pelo para mujer?",
        "landing.slider.example1.reply": "¡Hola! Los sábados abrimos de 9 a 16.\nUn corte de pelo para mujer cuesta 35 €.\n¿Quieres que te reserve un horario?",
        "landing.slider.example2.question": "¿Hacen blanqueamiento dental? ¿Puedo ir sin cita o necesito pedir turno?",
        "landing.slider.example2.reply": "Sí, ofrecemos blanqueamiento dental por 120 €. Te recomendamos reservar con antelación para guardarte un turno. ¿Quieres que revise la disponibilidad de esta semana?",
        "landing.slider.example3.question": "Se me rompió la pantalla del iPhone 13, ¿cuánto cuesta arreglarla y cuánto tarda?",
        "landing.slider.example3.reply": "El cambio de pantalla del iPhone 13 cuesta 89 € y suele estar listo el mismo día si lo traes antes de las 14:00. ¿Quieres traerlo hoy?",
        "landing.slider.example4.question": "Se me gotea el grifo de la cocina, ¿puede venir alguien a revisarlo esta semana?",
        "landing.slider.example4.reply": "¡Lo siento mucho! Tenemos disponibilidad el miércoles y jueves por la tarde. La visita más inspección cuesta 25 €, y te presupuestamos cualquier repuesto antes de empezar. ¿Qué día te viene mejor?",
        "landing.slider.example5.question": "¿A qué hora es el check-in y se permiten mascotas?",
        "landing.slider.example5.reply": "El check-in es a partir de las 15:00 y el check-out hasta las 11:00.\nSí, las mascotas son bienvenidas, solo avísanos con antelación para preparar el espacio.",
        "landing.slider.example6.question": "¿Cuánto cuesta una llamada de estrategia de una hora y cómo puedo reservarla?",
        "landing.slider.example6.reply": "Una llamada de estrategia de una hora cuesta 90 €. Puedo enviarte un enlace de reserva con mis horarios libres de esta semana, ¿te lo envío ahora?",
        "landing.slider.example7.question": "¿El vestido midi verde viene en talla M? ¿Cuál es su política de devoluciones?",
        "landing.slider.example7.reply": "¡Sí, tenemos talla M disponible! Aceptamos devoluciones dentro de 14 días con la etiqueta puesta. ¿Quieres que te lo reserve?",
        "landing.slider.example8.question": "¿Cuánto cuesta una manicura semipermanente y hay que reservar con antelación?",
        "landing.slider.example8.reply": "Una manicura semipermanente cuesta 28 €. Recomendamos reservar con un día de antelación, sobre todo los fines de semana. ¿Reviso la disponibilidad de mañana?",
        "landing.slider.example9.question": "¿Cuánto cuesta el plan mensual y está bien si nunca he entrenado antes?",
        "landing.slider.example9.reply": "El plan mensual cuesta 80 € por 8 sesiones.\nNo hay problema si estás empezando: la primera sesión es una introducción suave para armar un plan a tu medida.",
        "landing.slider.example10.question": "¿Tienen disponibilidad para una boda el 12 de septiembre y qué incluye su paquete?",
        "landing.slider.example10.reply": "¡El 12 de septiembre sigue libre!\nEl paquete estándar incluye 6 horas de cobertura y más de 300 fotos editadas. ¿Quieres que te envíe todos los detalles?",
    },

    pt: {
        "landing.slider.time.now": "Agora mesmo",
        "landing.slider.time.m2": "Há 2 min",
        "landing.slider.time.m5": "Há 5 min",
        "landing.slider.time.m12": "Há 12 min",
        "landing.slider.time.h1": "Há 1 h",

        "landing.slider.example1.question": "Olá! Qual o horário de sábado e quanto custa um corte de cabelo feminino?",
        "landing.slider.example1.reply": "Olá! Aos sábados abrimos das 9h às 16h.\nUm corte feminino custa 35 €.\nQuer que eu marque um horário para você?",
        "landing.slider.example2.question": "Vocês fazem clareamento dental? Posso ir sem marcar ou preciso agendar?",
        "landing.slider.example2.reply": "Sim, oferecemos clareamento dental por 120 €. Recomendamos agendar com antecedência para garantirmos seu horário — quer que eu veja a disponibilidade desta semana?",
        "landing.slider.example3.question": "A tela do meu iPhone 13 quebrou, quanto custa o conserto e quanto tempo leva?",
        "landing.slider.example3.reply": "A troca de tela do iPhone 13 custa 89 € e geralmente fica pronta no mesmo dia se você trouxer até as 14h. Quer trazer hoje?",
        "landing.slider.example4.question": "A torneira da minha cozinha está vazando, alguém pode ir dar uma olhada esta semana?",
        "landing.slider.example4.reply": "Puxa, sinto muito! Temos horários livres na quarta e quinta à tarde. A visita com inspeção custa 25 €, e orçamos qualquer peça antes de começar. Qual dia é melhor pra você?",
        "landing.slider.example5.question": "Que horas é o check-in e vocês aceitam animais de estimação?",
        "landing.slider.example5.reply": "O check-in é a partir das 15h e o check-out até as 11h.\nSim, pets são bem-vindos — é só avisar com antecedência para prepararmos o espaço.",
        "landing.slider.example6.question": "Quanto custa uma chamada de estratégia de uma hora e como faço para agendar?",
        "landing.slider.example6.reply": "Uma chamada de estratégia de uma hora custa 90 €. Posso te mandar um link de agendamento com meus horários livres desta semana — quer que eu envie agora?",
        "landing.slider.example7.question": "O vestido midi verde tem no tamanho M? Qual é a política de trocas?",
        "landing.slider.example7.reply": "Sim, temos tamanho M disponível! Aceitamos trocas em até 14 dias, com a etiqueta ainda presa. Quer que eu reserve um para você?",
        "landing.slider.example8.question": "Quanto custa uma manicure em gel, e preciso agendar com antecedência?",
        "landing.slider.example8.reply": "Uma manicure em gel custa 28 €. Recomendamos agendar com um dia de antecedência, principalmente nos fins de semana — quer que eu veja a disponibilidade de amanhã?",
        "landing.slider.example9.question": "Qual o preço do plano mensal, e tudo bem se eu nunca treinei antes?",
        "landing.slider.example9.reply": "O plano mensal custa 80 € por 8 sessões.\nSem problema se você está começando agora — a primeira sessão é uma introdução leve para montarmos um plano para você.",
        "landing.slider.example10.question": "Vocês têm disponibilidade para um casamento no dia 12 de setembro, e o que está incluso no pacote?",
        "landing.slider.example10.reply": "O dia 12 de setembro ainda está livre!\nO pacote padrão inclui 6 horas de cobertura e mais de 300 fotos editadas. Quer que eu envie todos os detalhes do pacote?",
    },

    fr: {
        "landing.slider.time.now": "À l'instant",
        "landing.slider.time.m2": "Il y a 2 min",
        "landing.slider.time.m5": "Il y a 5 min",
        "landing.slider.time.m12": "Il y a 12 min",
        "landing.slider.time.h1": "Il y a 1 h",

        "landing.slider.example1.question": "Bonjour ! Quels sont vos horaires le samedi, et combien coûte une coupe femme ?",
        "landing.slider.example1.reply": "Bonjour ! Nous sommes ouverts le samedi de 9h à 16h.\nUne coupe femme coûte 35 €.\nVoulez-vous que je vous réserve un créneau ?",
        "landing.slider.example2.question": "Faites-vous le blanchiment dentaire ? Puis-je venir sans rendez-vous ou dois-je en prendre un ?",
        "landing.slider.example2.reply": "Oui, nous proposons le blanchiment dentaire à 120 €. Nous recommandons de réserver à l'avance pour vous garder un créneau — voulez-vous que je regarde les disponibilités de cette semaine ?",
        "landing.slider.example3.question": "L'écran de mon iPhone 13 est cassé, combien coûte la réparation et combien de temps ça prend ?",
        "landing.slider.example3.reply": "Le remplacement d'écran d'iPhone 13 coûte 89 €, généralement prêt le jour même si déposé avant 14h. Voulez-vous l'apporter aujourd'hui ?",
        "landing.slider.example4.question": "Mon robinet de cuisine fuit, quelqu'un peut-il venir jeter un œil cette semaine ?",
        "landing.slider.example4.reply": "Désolé de l'apprendre ! Nous avons des créneaux mercredi et jeudi après-midi. Le déplacement avec inspection coûte 25 €, et nous chiffrons les pièces avant de commencer. Quel jour vous arrange le mieux ?",
        "landing.slider.example5.question": "À quelle heure est l'arrivée, et les animaux sont-ils acceptés ?",
        "landing.slider.example5.reply": "L'arrivée est possible à partir de 15h, le départ avant 11h.\nOui, les animaux sont les bienvenus — dites-le-nous simplement à l'avance pour qu'on prépare le logement.",
        "landing.slider.example6.question": "Combien coûte un appel stratégie d'une heure, et comment je réserve ?",
        "landing.slider.example6.reply": "Un appel stratégie d'une heure coûte 90 €. Je peux vous envoyer un lien de réservation avec mes créneaux libres cette semaine — je vous l'envoie maintenant ?",
        "landing.slider.example7.question": "La robe midi verte existe-t-elle en taille M, et quelle est votre politique de retour ?",
        "landing.slider.example7.reply": "Oui, la taille M est disponible ! Les retours sont acceptés sous 14 jours, étiquette encore attachée. Voulez-vous que je vous en mette une de côté ?",
        "landing.slider.example8.question": "Combien coûte une manucure au gel, et faut-il réserver à l'avance ?",
        "landing.slider.example8.reply": "Une manucure au gel coûte 28 €. Nous recommandons de réserver un jour à l'avance, surtout le week-end — je regarde les disponibilités de demain ?",
        "landing.slider.example9.question": "Quel est le prix de l'abonnement mensuel, et est-ce grave si je n'ai jamais fait de sport ?",
        "landing.slider.example9.reply": "L'abonnement mensuel coûte 80 € pour 8 séances.\nAucun souci si vous débutez — la première séance est une prise en main en douceur pour construire un programme adapté à vous.",
        "landing.slider.example10.question": "Avez-vous une disponibilité pour un mariage le 12 septembre, et qu'est-ce qui est inclus dans votre forfait ?",
        "landing.slider.example10.reply": "Le 12 septembre est encore libre !\nLe forfait standard comprend 6 heures de couverture et plus de 300 photos retouchées. Voulez-vous que je vous envoie tous les détails ?",
    },

    de: {
        "landing.slider.time.now": "Gerade eben",
        "landing.slider.time.m2": "Vor 2 Min.",
        "landing.slider.time.m5": "Vor 5 Min.",
        "landing.slider.time.m12": "Vor 12 Min.",
        "landing.slider.time.h1": "Vor 1 Std.",

        "landing.slider.example1.question": "Hallo! Wie sind eure Öffnungszeiten am Samstag, und was kostet ein Damenhaarschnitt?",
        "landing.slider.example1.reply": "Hallo! Samstags haben wir von 9 bis 16 Uhr geöffnet.\nEin Damenhaarschnitt kostet 35 €.\nSoll ich dir einen Termin buchen?",
        "landing.slider.example2.question": "Bietet ihr Zahnaufhellung an? Kann ich einfach vorbeikommen oder brauche ich einen Termin?",
        "landing.slider.example2.reply": "Ja, wir bieten Zahnaufhellung für 120 € an. Wir empfehlen eine Vorab-Buchung, damit wir dir einen Termin freihalten können — soll ich schauen, was diese Woche frei ist?",
        "landing.slider.example3.question": "Mein iPhone-13-Display ist gesprungen — was kostet die Reparatur und wie lange dauert sie?",
        "landing.slider.example3.reply": "Ein Displaytausch beim iPhone 13 kostet 89 € und ist meist noch am selben Tag fertig, wenn du es vor 14 Uhr vorbeibringst. Möchtest du es heute vorbeibringen?",
        "landing.slider.example4.question": "Mein Küchenhahn tropft — kann diese Woche jemand vorbeikommen und schauen?",
        "landing.slider.example4.reply": "Das tut mir leid! Wir haben Mittwoch- und Donnerstagnachmittag noch Termine frei. Anfahrt plus Begutachtung kostet 25 €, Ersatzteile besprechen wir vorab. Welcher Tag passt dir besser?",
        "landing.slider.example5.question": "Um wie viel Uhr ist der Check-in, und sind Haustiere erlaubt?",
        "landing.slider.example5.reply": "Check-in ist ab 15 Uhr, Check-out bis 11 Uhr.\nJa, Haustiere sind willkommen — sag uns einfach vorher Bescheid, damit wir alles vorbereiten können.",
        "landing.slider.example6.question": "Was kostet ein einstündiges Strategiegespräch, und wie buche ich es?",
        "landing.slider.example6.reply": "Ein einstündiges Strategiegespräch kostet 90 €. Ich kann dir einen Buchungslink mit meinen freien Terminen dieser Woche schicken — soll ich ihn jetzt senden?",
        "landing.slider.example7.question": "Gibt es das grüne Midikleid in Größe M, und wie ist eure Rückgaberegelung?",
        "landing.slider.example7.reply": "Ja, Größe M ist vorrätig! Rückgaben sind innerhalb von 14 Tagen mit Etikett möglich. Soll ich dir eins zurücklegen?",
        "landing.slider.example8.question": "Was kostet eine Gel-Maniküre, und muss ich vorher einen Termin buchen?",
        "landing.slider.example8.reply": "Eine Gel-Maniküre kostet 28 €. Wir empfehlen, einen Tag vorher zu buchen, besonders am Wochenende — soll ich schauen, was morgen frei ist?",
        "landing.slider.example9.question": "Was kostet das Monatsabo, und ist es okay, wenn ich noch nie trainiert habe?",
        "landing.slider.example9.reply": "Das Monatsabo kostet 80 € für 8 Einheiten.\nKein Problem, wenn du gerade erst anfängst — die erste Einheit ist ein sanfter Einstieg, damit wir einen Plan für dich erstellen können.",
        "landing.slider.example10.question": "Habt ihr am 12. September für eine Hochzeit noch etwas frei, und was ist im Paket enthalten?",
        "landing.slider.example10.reply": "Der 12. September ist noch frei!\nDas Standardpaket umfasst 6 Stunden Begleitung und über 300 bearbeitete Fotos. Soll ich dir alle Details zum Paket schicken?",
    },

    it: {
        "landing.slider.time.now": "Proprio ora",
        "landing.slider.time.m2": "2 min fa",
        "landing.slider.time.m5": "5 min fa",
        "landing.slider.time.m12": "12 min fa",
        "landing.slider.time.h1": "1 ora fa",

        "landing.slider.example1.question": "Ciao! Che orari fate il sabato, e quanto costa un taglio da donna?",
        "landing.slider.example1.reply": "Ciao! Il sabato siamo aperti dalle 9 alle 16.\nUn taglio da donna costa 35 €.\nVuoi che ti prenoti un orario?",
        "landing.slider.example2.question": "Fate lo sbiancamento dentale? Posso venire senza appuntamento o devo prenotare?",
        "landing.slider.example2.reply": "Sì, offriamo lo sbiancamento dentale a 120 €. Ti consigliamo di prenotare in anticipo per tenerti un posto — vuoi che controlli la disponibilità di questa settimana?",
        "landing.slider.example3.question": "Lo schermo del mio iPhone 13 si è rotto — quanto costa ripararlo e quanto tempo ci vuole?",
        "landing.slider.example3.reply": "La sostituzione dello schermo dell'iPhone 13 costa 89 € ed è di solito pronta in giornata se lo porti entro le 14. Vuoi portarlo oggi?",
        "landing.slider.example4.question": "Il rubinetto della cucina perde — può venire qualcuno a dare un'occhiata questa settimana?",
        "landing.slider.example4.reply": "Mi dispiace! Abbiamo disponibilità mercoledì e giovedì pomeriggio. Il sopralluogo con ispezione costa 25 €, e ti diamo un preventivo per eventuali pezzi prima di iniziare. Che giorno preferisci?",
        "landing.slider.example5.question": "A che ora è il check-in, e si possono portare animali?",
        "landing.slider.example5.reply": "Il check-in è dalle 15:00, il check-out entro le 11:00.\nSì, gli animali sono benvenuti — basta avvisarci in anticipo per preparare lo spazio.",
        "landing.slider.example6.question": "Quanto costa una call strategica di un'ora, e come si prenota?",
        "landing.slider.example6.reply": "Una call strategica di un'ora costa 90 €. Posso mandarti un link per prenotare tra i miei orari liberi questa settimana — te lo mando ora?",
        "landing.slider.example7.question": "Il vestito midi verde è disponibile in taglia M, e qual è la vostra politica di reso?",
        "landing.slider.example7.reply": "Sì, la taglia M è disponibile! I resi sono accettati entro 14 giorni con l'etichetta ancora attaccata. Vuoi che te ne tenga uno da parte?",
        "landing.slider.example8.question": "Quanto costa una manicure gel, e devo prenotare in anticipo?",
        "landing.slider.example8.reply": "Una manicure gel costa 28 €. Ti consigliamo di prenotare un giorno prima, specialmente nel weekend — controllo la disponibilità di domani?",
        "landing.slider.example9.question": "Quanto costa l'abbonamento mensile, e va bene se non mi sono mai allenato/a prima?",
        "landing.slider.example9.reply": "L'abbonamento mensile costa 80 € per 8 sessioni.\nNessun problema se stai iniziando ora — la prima sessione è un'introduzione leggera per costruire un piano su misura per te.",
        "landing.slider.example10.question": "Avete disponibilità per un matrimonio il 12 settembre, e cosa include il vostro pacchetto?",
        "landing.slider.example10.reply": "Il 12 settembre è ancora libero!\nIl pacchetto standard include 6 ore di copertura e oltre 300 foto ritoccate. Vuoi che ti mandi tutti i dettagli del pacchetto?",
    },

    ru: {
        "landing.slider.time.now": "Только что",
        "landing.slider.time.m2": "2 мин назад",
        "landing.slider.time.m5": "5 мин назад",
        "landing.slider.time.m12": "12 мин назад",
        "landing.slider.time.h1": "1 ч назад",

        "landing.slider.example1.question": "Привет! А в какое время вы работаете по субботам, и сколько стоит женская стрижка?",
        "landing.slider.example1.reply": "Привет! По субботам мы открыты с 9 до 16.\nЖенская стрижка стоит 35 €.\nЗаписать вас на удобное время?",
        "landing.slider.example2.question": "Вы делаете отбеливание зубов? Можно просто прийти или нужна запись?",
        "landing.slider.example2.reply": "Да, отбеливание зубов стоит 120 €. Рекомендуем записаться заранее, чтобы забронировать время — посмотреть, что свободно на этой неделе?",
        "landing.slider.example3.question": "У меня разбит экран на iPhone 13 — сколько стоит ремонт и сколько это займёт?",
        "landing.slider.example3.reply": "Замена экрана iPhone 13 стоит 89 €, обычно готово в тот же день, если сдать телефон до 14:00. Хотите принести его сегодня?",
        "landing.slider.example4.question": "У меня течёт кран на кухне — может кто-нибудь приехать посмотреть на этой неделе?",
        "landing.slider.example4.reply": "Очень жаль это слышать! У нас есть свободное время в среду и четверг после обеда. Выезд с осмотром стоит 25 €, а стоимость деталей мы озвучим до начала работы. Какой день вам удобнее?",
        "landing.slider.example5.question": "Во сколько заезд, и можно ли с домашними животными?",
        "landing.slider.example5.reply": "Заезд с 15:00, выезд до 11:00.\nДа, с животными можно — просто предупредите заранее, чтобы мы всё подготовили.",
        "landing.slider.example6.question": "Сколько стоит часовая стратегическая консультация и как записаться?",
        "landing.slider.example6.reply": "Часовая стратегическая консультация стоит 90 €. Могу прислать ссылку для записи на свободное время этой недели — отправить сейчас?",
        "landing.slider.example7.question": "Есть ли зелёное миди-платье в размере M, и какая у вас политика возврата?",
        "landing.slider.example7.reply": "Да, размер M есть в наличии! Возврат возможен в течение 14 дней с сохранённой биркой. Отложить для вас одно?",
        "landing.slider.example8.question": "Сколько стоит гель-маникюр и нужно ли записываться заранее?",
        "landing.slider.example8.reply": "Гель-маникюр стоит 28 €. Рекомендуем записываться за день, особенно на выходные — посмотреть, что свободно завтра?",
        "landing.slider.example9.question": "Сколько стоит месячный абонемент, и ничего, если я никогда раньше не тренировался(-ась)?",
        "landing.slider.example9.reply": "Месячный абонемент стоит 80 € за 8 занятий.\nНичего страшного, если вы только начинаете — первое занятие будет лёгким вводным, чтобы составить план именно под вас.",
        "landing.slider.example10.question": "Есть ли у вас свободная дата на свадьбу 12 сентября, и что входит в ваш пакет услуг?",
        "landing.slider.example10.reply": "12 сентября пока свободно!\nСтандартный пакет включает 6 часов съёмки и более 300 обработанных фото. Прислать вам все детали пакета?",
    },

    sr: {
        "landing.slider.time.now": "Управо сада",
        "landing.slider.time.m2": "Пре 2 мин",
        "landing.slider.time.m5": "Пре 5 мин",
        "landing.slider.time.m12": "Пре 12 мин",
        "landing.slider.time.h1": "Пре 1 ч",

        "landing.slider.example1.question": "Ћао! Које је радно време суботом, и колико кошта женско шишање?",
        "landing.slider.example1.reply": "Ћао! Суботом радимо од 9 до 16.\nЖенско шишање кошта 35 €.\nДа ли да вас закажем за неки термин?",
        "landing.slider.example2.question": "Да ли радите изблеђивање зуба? Могу ли само да свратим или морам да закажем термин?",
        "landing.slider.example2.reply": "Да, изблеђивање зуба кошта 120 €. Препоручујемо да закажете унапред како бисмо вам резервисали термин — да проверим шта имамо слободно ове недеље?",
        "landing.slider.example3.question": "Пукао ми је екран на iPhone 13 — колико кошта поправка и колико траје?",
        "landing.slider.example3.reply": "Замена екрана за iPhone 13 кошта 89 €, обично готово истог дана ако донесете пре 14ч. Да ли желите да га донесете данас?",
        "landing.slider.example4.question": "Курим ми славина у кухињи — да ли неко може да сврати да погледа ове недеље?",
        "landing.slider.example4.reply": "Жао ми је! Имамо слободно среду и четвртак после подне. Излазак са прегледом кошта 25 €, а за евентуалне делове ћемо вам дати понуду пре него што почнемо. Који дан вам више одговара?",
        "landing.slider.example5.question": "У колико сати је check-in, и да ли су кућни љубимци дозвољени?",
        "landing.slider.example5.reply": "Check-in је од 15:00, check-out до 11:00.\nДа, кућни љубимци су добродошли — само нам јавите унапред да припремимо простор.",
        "landing.slider.example6.question": "Колико кошта једносатни стратешки позив, и како могу да закажем?",
        "landing.slider.example6.reply": "Једносатни стратешки позив кошта 90 €. Могу вам послати линк за заказивање са мојим слободним терминима ове недеље — да пошаљем сада?",
        "landing.slider.example7.question": "Да ли зелена миди хаљина долази у величини M, и каква вам је политика повраћаја?",
        "landing.slider.example7.reply": "Да, величина M је на стању! Повраћај прихватамо у року од 14 дана, уз причвршћену етикету. Да вам оставим једну по страни?",
        "landing.slider.example8.question": "Колико кошта гел маникир, и да ли треба да закажем унапред?",
        "landing.slider.example8.reply": "Гел маникир кошта 28 €. Препоручујемо да закажете дан унапред, посебно викендом — да проверим шта имамо слободно сутра?",
        "landing.slider.example9.question": "Колика је цена месечног пакета, и да ли је у реду ако никад раније нисам тренирао/ла?",
        "landing.slider.example9.reply": "Месечни пакет кошта 80 € за 8 термина.\nНема проблема ако тек почињете — први термин је благ уводни термин како бисмо направили план прилагођен вама.",
        "landing.slider.example10.question": "Да ли имате слободан термин за венчање 12. септембра, и шта укључује ваш пакет?",
        "landing.slider.example10.reply": "12. септембар је још увек слободан!\nСтандардни пакет укључује 6 сати снимања и преко 300 обрађених фотографија. Да вам пошаљем све детаље пакета?",
    },

    zh: {
        "landing.slider.time.now": "刚刚",
        "landing.slider.time.m2": "2分钟前",
        "landing.slider.time.m5": "5分钟前",
        "landing.slider.time.m12": "12分钟前",
        "landing.slider.time.h1": "1小时前",

        "landing.slider.example1.question": "你好!请问周六营业时间是几点,女士理发多少钱?",
        "landing.slider.example1.reply": "你好!我们周六9点到16点营业。\n女士理发是35欧元。\n需要我帮您预约时间吗?",
        "landing.slider.example2.question": "你们提供牙齿美白服务吗?可以直接过来还是需要预约?",
        "landing.slider.example2.reply": "是的,牙齿美白服务是120欧元。建议提前预约以便为您保留名额——需要我看看本周还有哪些空档吗?",
        "landing.slider.example3.question": "我的iPhone 13屏幕碎了,维修要多少钱,需要多长时间?",
        "landing.slider.example3.reply": "iPhone 13换屏是89欧元,如果下午2点前送来,通常当天就能修好。您今天要送过来吗?",
        "landing.slider.example4.question": "我家厨房的水龙头漏水了,这周能有人过来看看吗?",
        "landing.slider.example4.reply": "很抱歉听到这个消息!我们周三和周四下午有空。上门检查费是25欧元,如需更换零件会在开工前先报价。您哪天比较方便?",
        "landing.slider.example5.question": "请问几点可以入住,可以带宠物吗?",
        "landing.slider.example5.reply": "入住时间为15:00起,退房时间为11:00前。\n可以带宠物,提前告诉我们一声,我们会做好准备。",
        "landing.slider.example6.question": "一小时的策略咨询多少钱,怎么预约?",
        "landing.slider.example6.reply": "一小时的策略咨询是90欧元。我可以把本周空闲时间的预约链接发给您——现在发送吗?",
        "landing.slider.example7.question": "绿色中长裙有M码吗?你们的退货政策是怎样的?",
        "landing.slider.example7.reply": "有的,M码有现货!14天内且吊牌完好可以退货。需要我帮您留一件吗?",
        "landing.slider.example8.question": "凝胶美甲多少钱,需要提前预约吗?",
        "landing.slider.example8.reply": "凝胶美甲是28欧元。建议提前一天预约,尤其是周末——需要我看看明天的空档吗?",
        "landing.slider.example9.question": "月度套餐多少钱?如果我从来没训练过也可以吗?",
        "landing.slider.example9.reply": "月度套餐是80欧元,包含8节课。\n刚开始训练完全没问题——第一节课是轻松的入门课,方便我们为您量身定制计划。",
        "landing.slider.example10.question": "9月12日的婚礼还有档期吗?你们的套餐都包含什么?",
        "landing.slider.example10.reply": "9月12日还有档期!\n标准套餐包含6小时跟拍和300多张精修照片。需要我把完整的套餐详情发给您吗?",
    },

    tr: {
        "landing.slider.time.now": "Az önce",
        "landing.slider.time.m2": "2 dk önce",
        "landing.slider.time.m5": "5 dk önce",
        "landing.slider.time.m12": "12 dk önce",
        "landing.slider.time.h1": "1 sa önce",

        "landing.slider.example1.question": "Merhaba! Cumartesi çalışma saatleriniz nedir, kadın saç kesimi ne kadar?",
        "landing.slider.example1.reply": "Merhaba! Cumartesi günleri 9-16 arası açığız.\nKadın saç kesimi 35 €.\nSizin için bir randevu ayarlayayım mı?",
        "landing.slider.example2.question": "Diş beyazlatma yapıyor musunuz? Randevusuz gelebilir miyim, yoksa randevu almam mı gerekiyor?",
        "landing.slider.example2.reply": "Evet, diş beyazlatma hizmetimiz 120 €. Size yer ayırabilmemiz için önceden randevu almanızı öneririz — bu hafta ne zaman müsait olduğumuza bakayım mı?",
        "landing.slider.example3.question": "iPhone 13'ümün ekranı kırıldı, tamiri ne kadar tutar ve ne kadar sürer?",
        "landing.slider.example3.reply": "iPhone 13 ekran değişimi 89 € ve saat 14:00'ten önce bırakırsanız genelde aynı gün hazır olur. Bugün getirmek ister misiniz?",
        "landing.slider.example4.question": "Mutfak muslugum damlıyor, bu hafta birisi gelip bakabilir mi?",
        "landing.slider.example4.reply": "Bunu duyduğuma üzüldüm! Çarşamba ve perşembe öğleden sonra müsaitliğimiz var. Ziyaret ve inceleme 25 €, gerekli parçalar için işe başlamadan önce fiyat vereceğiz. Hangi gün size daha uygun?",
        "landing.slider.example5.question": "Check-in saat kaçta, ve evcil hayvana izin veriyor musunuz?",
        "landing.slider.example5.reply": "Check-in saat 15:00'ten itibaren, check-out saat 11:00'e kadar.\nEvet, evcil hayvanlar kabul ediliyor — sadece önceden haber verin ki mekanı hazırlayalım.",
        "landing.slider.example6.question": "Bir saatlik strateji görüşmesi ne kadar, ve nasıl randevu alabilirim?",
        "landing.slider.example6.reply": "Bir saatlik strateji görüşmesi 90 €. Bu haftaki boş saatlerimi gösteren bir randevu bağlantısı gönderebilirim — şimdi göndereyim mi?",
        "landing.slider.example7.question": "Yeşil midi elbise M beden var mı, ve iade politikanız nedir?",
        "landing.slider.example7.reply": "Evet, M beden mevcut! Etiketi üzerinde olmak kaydıyla 14 gün içinde iade kabul ediyoruz. Sizin için bir tane ayırayım mı?",
        "landing.slider.example8.question": "Jel manikür ne kadar, ve önceden randevu almam gerekiyor mu?",
        "landing.slider.example8.reply": "Jel manikür 28 €. Özellikle hafta sonları için bir gün önceden randevu almanızı öneririz — yarın için müsaitliğe bakayım mı?",
        "landing.slider.example9.question": "Aylık paketin fiyatı nedir, ve daha önce hiç antrenman yapmadıysam sorun olur mu?",
        "landing.slider.example9.reply": "Aylık paket 8 seans için 80 €.\nYeni başlıyor olmanız hiç sorun değil — ilk seans, size özel bir program oluşturabilmemiz için hafif bir tanışma seansı olacak.",
        "landing.slider.example10.question": "12 Eylül'de bir düğün için müsaitliğiniz var mı, ve paketinize neler dahil?",
        "landing.slider.example10.reply": "12 Eylül hâlâ müsait!\nStandart paket 6 saatlik çekim ve 300'den fazla düzenlenmiş fotoğraf içeriyor. Tüm paket detaylarını göndereyim mi?",
    },
};

function t(key) {
    const lang = TRANSLATIONS[currentLang] || TRANSLATIONS.en;
    return lang[key] || TRANSLATIONS.en[key] || key;
}

let currentLang = "en";

function applyTranslations() {
    document.documentElement.lang = currentLang;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
        node.textContent = t(node.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
        node.placeholder = t(node.getAttribute("data-i18n-placeholder"));
    });
    const lang = LANGUAGES.find((l) => l.code === currentLang);
    const btn = document.getElementById("site-lang-select-btn");
    const flagEl = document.getElementById("site-lang-select-flag");
    if (flagEl && lang) flagEl.src = `/static/flags/${lang.flag}`;
    if (btn && lang) {
        btn.title = lang.name;
        btn.setAttribute("aria-label", `Change language (current: ${lang.name})`);
    }
    document.querySelectorAll(".site-lang-option-btn").forEach((row) => {
        row.classList.toggle("active", row.dataset.code === currentLang);
    });
}

function setLanguage(code) {
    if (!TRANSLATIONS[code]) return;
    currentLang = code;
    try { localStorage.setItem("siteLanguage", code); } catch (e) {}
    applyTranslations();
}

function initLanguage() {
    let saved = null;
    try { saved = localStorage.getItem("siteLanguage"); } catch (e) {}
    if (saved && TRANSLATIONS[saved]) {
        currentLang = saved;
    } else {
        // Best-effort default from the browser's own language, same fallback logic as
        // the mobile app - falls back to English (not Serbian, unlike mobile's phone-
        // number-country guess) since the website has no equivalent "home market" signal.
        const guess = (navigator.language || "").slice(0, 2).toLowerCase();
        if (TRANSLATIONS[guess]) currentLang = guess;
    }
    applyTranslations();
}

function setupLanguagePicker() {
    const btn = document.getElementById("site-lang-select-btn");
    const modal = document.getElementById("site-lang-modal");
    const backdrop = document.getElementById("site-lang-modal-backdrop");
    const list = document.getElementById("site-lang-modal-list");
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
        row.className = "site-lang-option-btn";
        row.dataset.code = lang.code;
        const flagImg = document.createElement("img");
        flagImg.className = "site-lang-option-flag";
        flagImg.src = `/static/flags/${lang.flag}`;
        flagImg.alt = "";
        const nameSpan = document.createElement("span");
        nameSpan.className = "site-lang-option-name";
        nameSpan.textContent = lang.name;
        row.append(flagImg, nameSpan);
        row.addEventListener("click", () => {
            setLanguage(lang.code);
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

document.addEventListener("DOMContentLoaded", () => {
    setupLanguagePicker();
    initLanguage();
});

// The main nav (_nav.html) is hx-boosted - clicking Conversations/Profile/Settings/
// Subscription swaps htmx's default target (document.body's innerHTML) via AJAX instead
// of a full page load, so DOMContentLoaded (which only fires once) never re-fires for
// it. That swap replaces the picker button/modal too (they're rendered as part of every
// page's <body> via base.html, same as the content), leaving fresh DOM nodes with no
// click listeners and default English text - re-running both setup steps against
// whatever's currently in the DOM fixes both.
document.body.addEventListener("htmx:afterSwap", () => {
    setupLanguagePicker();
    applyTranslations();
});
