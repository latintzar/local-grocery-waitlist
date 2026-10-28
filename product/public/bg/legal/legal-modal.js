// Vitrina shared legal modal — Privacy + Terms (EN+BG).
//
// Single source of truth so /drop-01-v13, /waitlist, and any other public
// surface render the SAME policy text. The content here is the actual legal
// disclosure; updating it here updates every page that includes this script.
//
// Usage:
//   <script src="/legal/legal-modal.js"></script>
//   <a href="#" onclick="event.preventDefault();VitrinaLegal.open('privacy')">Privacy</a>
//   <a href="#" onclick="event.preventDefault();VitrinaLegal.open('terms')">Terms</a>
//
// The modal is fully self-contained: builds its own overlay on first open,
// uses inline styles, picks language from <html lang>, escapes Esc + click-out.
//
// NOTE: drop-01-v13/index.html still has its own inline copy of LEGAL_COPY
// (kept in sync with this file by hand for now). When it's next touched,
// migrate it to use this shared module via VitrinaLegal.open(...).
(function (global) {
  'use strict';

  const LEGAL_COPY = {
  en: {
    privacy: {
      title: 'Privacy notice',
      body: `
        <p><strong>Last updated: 7 May 2026.</strong> This notice tells you exactly what personal data we collect when you join the Vitrina waitlist, why we collect it, who can see it, how long we keep it, and how to make us stop. We have written it in plain language because the people reading it are our future customers.</p>

        <h3>1. Who is the controller</h3>
        <p><strong>Leo Casa Ltd</strong> (the company) operates Vitrina. We are a UK-incorporated company at Suite 402, 2 Old Brompton Road, London SW7 3DQ, United Kingdom. We are running a closed beta in Bulgaria pending registration with the Bulgarian Food Safety Agency (БАБХ) and the Commission for Consumer Protection (КЗП). Because we offer goods to people in Bulgaria the EU GDPR (Regulation 2016/679) applies to this processing in addition to UK GDPR. The Bulgarian Personal Data Protection Act (ЗЗЛД) also applies.</p>
        <p>We have not appointed a Data Protection Officer because we are not required to under Article 37 GDPR. The founder, Peter Neyra, is the single human inside the company who handles personal data. Reach him at <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a>.</p>

        <h3>2. What we collect</h3>
        <ul>
          <li><strong>Identity &amp; contact:</strong> first name, phone number (E.164), email, your preferred contact channel (WhatsApp, Viber, SMS, call, email), language preference.</li>
          <li><strong>Location for delivery:</strong> home address (street + neighbourhood) and, if you give it, your work address; the rough lat/long pin we get from the address picker.</li>
          <li><strong>Workplace:</strong> if you ask us to deliver to work, the company name at that address.</li>
          <li><strong>Taste &amp; preferences:</strong> the answers you give to our short product survey.</li>
          <li><strong>Conversation:</strong> if you message us on Instagram, your IG username and the messages themselves; if you book or accept a call, the notes Peter writes after.</li>
          <li><strong>How you found us:</strong> the referral code you came in on (if any).</li>
          <li><strong>Server logs:</strong> IP address, browser user-agent, and request timestamps. Standard for any website. We use these only for security and debugging.</li>
        </ul>

        <h3>3. Why we collect it &amp; the legal basis (Art. 6 GDPR)</h3>
        <ul>
          <li><strong>To run the waitlist queue and tell you your position</strong> — your <em>consent</em> (Art. 6(1)(a)), given by submitting the form.</li>
          <li><strong>To contact you about openings in your neighbourhood</strong>, on the channel(s) you ticked at signup — your <em>consent</em>, plus the soft opt-in under Art. 13(2) of the EU e-Privacy Directive as transposed into Bulgaria's Electronic Communications Act for marketing of similar products.</li>
          <li><strong>To deliver products if/when you actually order</strong> — performance of a <em>contract</em> (Art. 6(1)(b)).</li>
          <li><strong>To prevent abuse, fraud, and to keep the system secure</strong> — our <em>legitimate interest</em> (Art. 6(1)(f)).</li>
          <li><strong>To comply with Bulgarian commercial / tax / consumer-protection law</strong> when applicable — <em>legal obligation</em> (Art. 6(1)(c)).</li>
        </ul>
        <p>We do <strong>not</strong> use your data for automated decisions with legal or significant effect on you (Art. 22). No profiling for advertising. No price discrimination.</p>

        <h3>4. Who can see your data</h3>
        <p><strong>Inside Leo Casa Ltd:</strong> only the founder, Peter Neyra. There are no other employees today.</p>
        <p><strong>Service providers (processors) who store or transmit data on our instructions, each under a Data Processing Agreement (Art. 28 GDPR):</strong></p>
        <ul>
          <li><strong>Supabase Inc.</strong> — Postgres database + auth. EU region (Frankfurt, eu-central-1). DPA Q1 2026, EU SCCs cover any US affiliate access.</li>
          <li><strong>Resend Inc.</strong> — sends our transactional emails. US-based; certified under the EU-US Data Privacy Framework (since March 2025) and EU SCCs.</li>
          <li><strong>Vercel Inc.</strong> — hosts this website and our serverless API. US-based with EU edge regions; DPA + SCCs.</li>
          <li><strong>Hetzner Online GmbH</strong> — hosts our backend services. EU/US regions; DPA in place. Our backend currently runs in Ashburn, Virginia, USA, behind EU-fronting domains.</li>
          <li><strong>Meta Platforms Ireland Ltd</strong> — only if you message us on Instagram; we receive your message via the Instagram Graph API.</li>
          <li><strong>Stripe Payments Europe Ltd</strong> — <em>only</em> when you actually pay for an order, which does not happen on the waitlist.</li>
          <li><strong>PostHog Inc.</strong> — product analytics; EU host (eu.i.posthog.com). We do not share your contact data with PostHog; only product-usage events.</li>
          <li><strong>Mem0, Inc.</strong> — semantic memory of past conversations, used only when you chat with us.</li>
          <li><strong>Notion Labs Inc.</strong> &amp; <strong>Slack (Salesforce)</strong> — internal note-taking and notifications about new signups.</li>
          <li><strong>LLM providers</strong> (Anthropic, OpenAI, AWS Bedrock, OpenRouter — depending on configuration) — receive only the conversation content when you chat with our agent. Never the waitlist contact data.</li>
        </ul>
        <p>We do <strong>not</strong> sell, rent, swap, or share your data with advertisers, data brokers, marketing networks, or anyone else. There are no third-party advertising pixels on this site.</p>

        <h3>5. International transfers</h3>
        <p>Some of our processors are based outside the EEA (mostly in the US). For each such transfer we rely on (a) the European Commission's adequacy decision for the EU-US Data Privacy Framework where the processor is certified (Resend), and/or (b) the European Commission's Standard Contractual Clauses (Module 2, Controller-to-Processor). Copies are available on request at <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a>.</p>

        <h3>6. How long we keep it</h3>
        <ul>
          <li><strong>Waitlist row</strong> (name, phone, email, address, taste answers): until you ask us to delete it, or 24 months after your last interaction with us — whichever comes first.</li>
          <li><strong>Call notes &amp; transcripts</strong>: 12 months after the last contact, then deleted.</li>
          <li><strong>Instagram conversation history</strong>: 24 months from the last message.</li>
          <li><strong>Server logs</strong> (IP, user-agent): 90 days.</li>
          <li><strong>Order records</strong>, when you eventually place one: 10 years, because Bulgarian commercial and tax law requires it.</li>
        </ul>
        <p>When you ask us to delete you, we erase from our primary database and from semantic-memory (Mem0) within 30 days. Encrypted backups roll over within a further 30 days.</p>

        <h3>7. Your rights (Arts. 15–22 GDPR)</h3>
        <p>You can, free of charge:</p>
        <ul>
          <li><strong>Access</strong> a copy of the data we hold about you.</li>
          <li><strong>Rectify</strong> anything that is wrong.</li>
          <li><strong>Erase</strong> your data ("the right to be forgotten").</li>
          <li><strong>Restrict</strong> our processing while a question is being resolved.</li>
          <li><strong>Receive</strong> your data in a machine-readable format and ask us to transmit it to another controller (data portability).</li>
          <li><strong>Object</strong> to processing based on legitimate interests, or to direct marketing — at any time, no reason needed.</li>
          <li><strong>Withdraw consent</strong> at any time — withdrawal does not affect anything we did before.</li>
        </ul>
        <p>To exercise any of these, email <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a>. We will respond within one calendar month (Art. 12(3) GDPR), and almost always within a few days because there is one of us.</p>

        <h3>8. Right to lodge a complaint</h3>
        <p>If you think we are mishandling your data you can complain to the Bulgarian supervisory authority:</p>
        <p><strong>Commission for Personal Data Protection (КЗЛД / CPDP)</strong><br>
        2 Prof. Tsvetan Lazarov Blvd., Sofia 1592, Bulgaria<br>
        kzld@cpdp.bg · +359 2 915 3519 · <a href="https://cpdp.bg/en/" rel="noopener" target="_blank">cpdp.bg</a></p>
        <p>UK residents may instead complain to the UK Information Commissioner's Office (<a href="https://ico.org.uk" rel="noopener" target="_blank">ico.org.uk</a>).</p>

        <h3>9. Cookies and tracking</h3>
        <p>This website sets <em>only first-party functional storage</em> (a small piece of localStorage to remember your language choice and your waitlist token). We do not run third-party advertising cookies, fingerprinting, or cross-site trackers. PostHog, when enabled, sets a first-party cookie/localStorage entry to count anonymous product-usage events on our EU host.</p>

        <h3>10. Children</h3>
        <p>The waitlist is for adults. Bulgarian law sets the age of consent for information-society services at 14 (ZZLD Art. 25b). We do not knowingly collect data on anyone younger; if you are a parent and believe we have, email us and we will delete immediately.</p>

        <h3>11. Changes to this notice</h3>
        <p>If we change anything material we will (a) update the "Last updated" date at the top, (b) keep the previous version in our records, and (c) email everyone on the waitlist when the change matters to them.</p>

        <h3>12. Contact</h3>
        <p>Leo Casa Ltd (operating Vitrina)<br>
        Suite 402, 2 Old Brompton Road, London SW7 3DQ, United Kingdom<br>
        <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a></p>
      `,
    },
    terms: {
      title: 'Terms (closed beta)',
      body: `
        <p><strong>Last updated: 7 May 2026.</strong> Vitrina is run by <strong>Leo Casa Ltd</strong> (UK), operating a closed beta in Sofia. By joining the waitlist you agree to these terms. They are short on purpose.</p>

        <h3>1. What this is</h3>
        <p>You are joining a queue. We are not yet selling to the public — we are pending registration with the Bulgarian Food Safety Agency (БАБХ) and the Commission for Consumer Protection (КЗП). Joining the waitlist costs you nothing and obliges you to nothing.</p>

        <h3>2. Communications you are agreeing to</h3>
        <p>By submitting the form and ticking your preferred channels (WhatsApp, Viber, SMS, phone call, email) you consent to us contacting you on those channels about: (a) your position in the queue, (b) the first drops opening in your neighbourhood, and (c) related practical information (delivery windows, registration milestones, beta access). You can withdraw consent at any time by replying STOP, telling us in conversation, or emailing <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a> — and we will stop within 48 hours.</p>

        <h3>3. No payment is taken at signup</h3>
        <p>Joining the waitlist is free and never asks for a card. Money only changes hands if you actively confirm an order in a future drop that reaches its minimum.</p>

        <h3>4. How a future "drop" will work (preview, not yet operational)</h3>
        <p>Each drop has a minimum number of orders and a deadline. If we hit the minimum, your card is charged at wholesale price and the order ships. If we don't, the slot rolls — your card is never charged and nothing is shipped.</p>

        <h3>5. Limitation of liability</h3>
        <p>While we are in closed beta we provide the waitlist "as is". We are not liable for missed notifications caused by carrier or platform outages (WhatsApp, Viber, SMS, email). For statutory consumer rights under Bulgarian law: nothing in these terms reduces them.</p>

        <h3>6. Governing law</h3>
        <p>These beta terms are governed by the laws of England and Wales (the controller's home jurisdiction). Bulgarian consumers retain all mandatory protections of Bulgarian consumer-protection law.</p>

        <h3>7. Company</h3>
        <p>Leo Casa Ltd · UK-incorporated · Suite 402, 2 Old Brompton Road, London SW7 3DQ, United Kingdom · pending Bulgarian operating registrations (БАБХ + КЗП) · <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a></p>
      `,
    },
  },
  bg: {
    privacy: {
      title: 'Политика за поверителност',
      body: `
        <p><strong>Последна редакция: 7 май 2026 г.</strong> Този документ ти казва точно какви лични данни събираме, когато се запишеш в списъка с чакащи на Vitrina, защо ги събираме, кой може да ги вижда, колко дълго ги пазим и как да ни накараш да спрем. Написан е на разбираем език, защото хората, които го четат, са нашите бъдещи клиенти.</p>

        <h3>1. Кой е администраторът</h3>
        <p><strong>Leo Casa Ltd</strong> оперира Vitrina. Дружеството е регистрирано в Обединеното кралство — Suite 402, 2 Old Brompton Road, London SW7 3DQ. В момента работим в затворен бета режим в България, докато се регистрираме в Българската агенция по безопасност на храните (БАБХ) и Комисията за защита на потребителите (КЗП). Тъй като предлагаме стоки на хора в България, обработката попада под Регламент (ЕС) 2016/679 (GDPR), а също и под британския UK GDPR. Прилага се и българският Закон за защита на личните данни (ЗЗЛД).</p>
        <p>Не сме назначили длъжностно лице по защита на данните, защото не сме задължени по член 37 от GDPR. Основателят Петър Нейра е единственият човек в дружеството, който работи с лични данни. Можеш да го намериш на <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a>.</p>

        <h3>2. Какво събираме</h3>
        <ul>
          <li><strong>Идентификация и контакт:</strong> малко име, телефонен номер (във формат E.164), имейл, предпочитан канал за връзка (WhatsApp, Viber, SMS, обаждане, имейл), език.</li>
          <li><strong>Локация за доставка:</strong> домашен адрес (улица + квартал) и, ако пожелаеш, работен адрес; приблизителни координати, които вземаме от избора на адрес.</li>
          <li><strong>Работно място:</strong> ако искаш доставка на работа — името на фирмата на този адрес.</li>
          <li><strong>Вкус и предпочитания:</strong> отговорите ти на нашия кратък продуктов въпросник.</li>
          <li><strong>Кореспонденция:</strong> ако ни пишеш в Instagram — твоят IG потребител и самите съобщения; ако имаме разговор по телефона или среща, бележките, които Петър си води след това.</li>
          <li><strong>Как си ни намерил/а:</strong> реферален код, ако има.</li>
          <li><strong>Сървърни логове:</strong> IP адрес, user-agent на браузъра, времеви маркери. Стандартни за всеки уеб сайт. Използваме ги само за сигурност и отстраняване на грешки.</li>
        </ul>

        <h3>3. Защо ги събираме и на какво правно основание (чл. 6 GDPR)</h3>
        <ul>
          <li><strong>За да поддържаме опашката и да ти кажем мястото ти</strong> — твоето <em>съгласие</em> (чл. 6, ал. 1, б. „а“), дадено с попълването на формата.</li>
          <li><strong>За да се свържем с теб, когато отворим слот в твоя квартал</strong>, по канала/каналите, които си избрал/а — твоето <em>съгласие</em> плюс „soft opt-in“ изключението по чл. 13, ал. 2 от Директивата за електронната неприкосновеност, транспонирано в българския Закон за електронните съобщения, за маркетинг на сходни продукти.</li>
          <li><strong>За да ти доставим продукти, ако/когато направиш реална поръчка</strong> — изпълнение на <em>договор</em> (чл. 6, ал. 1, б. „б“).</li>
          <li><strong>За предотвратяване на злоупотреби и сигурност на системата</strong> — наш <em>законен интерес</em> (чл. 6, ал. 1, б. „е“).</li>
          <li><strong>За изпълнение на търговското, данъчното и потребителското законодателство</strong>, когато е приложимо — <em>законово задължение</em> (чл. 6, ал. 1, б. „в“).</li>
        </ul>
        <p><strong>Не</strong> използваме данните ти за автоматизирани решения с правен или значителен ефект върху теб (чл. 22). Без профилиране за реклама. Без ценова дискриминация.</p>

        <h3>4. Кой вижда данните ти</h3>
        <p><strong>Вътре в Leo Casa Ltd:</strong> само основателят Петър Нейра. В момента няма други служители.</p>
        <p><strong>Доставчици на услуги (обработващи лични данни), които съхраняват или предават данни по наше указание, всеки по силата на Споразумение за обработка (чл. 28 GDPR):</strong></p>
        <ul>
          <li><strong>Supabase Inc.</strong> — Postgres база данни + автентикация. Регион ЕС (Франкфурт, eu-central-1). DPA Q1 2026, стандартни договорни клаузи (SCCs) на ЕС покриват евентуален достъп от американски филиал.</li>
          <li><strong>Resend Inc.</strong> — изпраща нашите транзакционни имейли. Базирана в САЩ; сертифицирана по EU-US Data Privacy Framework (от март 2025) и EU SCCs.</li>
          <li><strong>Vercel Inc.</strong> — хоства уеб сайта и serverless API. Базирана в САЩ с edge региони в ЕС; DPA + SCCs.</li>
          <li><strong>Hetzner Online GmbH</strong> — хоства backend услугите. EU/US региони; DPA. Текущо backend-ът ни работи в Ashburn, Virginia, САЩ, зад домейни с фронт в ЕС.</li>
          <li><strong>Meta Platforms Ireland Ltd</strong> — само ако ни пишеш в Instagram; получаваме съобщението ти през Instagram Graph API.</li>
          <li><strong>Stripe Payments Europe Ltd</strong> — <em>само</em> когато реално платиш поръчка, което не се случва на етапа на списъка с чакащи.</li>
          <li><strong>PostHog Inc.</strong> — продуктова аналитика; ЕС хост (eu.i.posthog.com). Не споделяме контактните ти данни с PostHog; само анонимни събития за ползване.</li>
          <li><strong>Mem0, Inc.</strong> — семантична памет за минали разговори, използвана само когато си говорим.</li>
          <li><strong>Notion Labs Inc.</strong> и <strong>Slack (Salesforce)</strong> — вътрешни бележки и нотификации за нови записвания.</li>
          <li><strong>LLM доставчици</strong> (Anthropic, OpenAI, AWS Bedrock, OpenRouter — според конфигурацията) — получават само съдържанието на разговора, когато си чатиш с нашия агент. Никога контактните ти данни от списъка.</li>
        </ul>
        <p><strong>Не</strong> продаваме, не отдаваме и не разменяме твоите данни с рекламодатели, брокери на данни, маркетинг мрежи или други. На сайта няма пиксели за реклама от трети страни.</p>

        <h3>5. Международни трансфери</h3>
        <p>Някои от нашите обработващи са извън ЕИП (предимно в САЩ). За всеки такъв трансфер разчитаме на (а) решението на Европейската комисия за адекватност по Data Privacy Framework, когато доставчикът е сертифициран (Resend), и/или (б) Стандартните договорни клаузи (Модул 2, администратор-обработващ). Копия — при поискване на <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a>.</p>

        <h3>6. Колко дълго пазим данните</h3>
        <ul>
          <li><strong>Запис в списъка с чакащи</strong> (име, телефон, имейл, адрес, отговори от въпросника): докато не поискаш изтриване или 24 месеца след последното ти взаимодействие — което настъпи първо.</li>
          <li><strong>Бележки и транскрипции от разговори</strong>: 12 месеца след последния контакт.</li>
          <li><strong>История на Instagram чатове</strong>: 24 месеца от последното съобщение.</li>
          <li><strong>Сървърни логове</strong> (IP, user-agent): 90 дни.</li>
          <li><strong>Поръчки</strong>, ако направиш такава в бъдеще: 10 години, защото българското търговско и данъчно законодателство го изисква.</li>
        </ul>
        <p>Когато поискаш изтриване, премахваме данните от основната база и от семантичната памет (Mem0) в рамките на 30 дни. Криптираните резервни копия се обновяват в следващите 30 дни.</p>

        <h3>7. Твоите права (чл. 15–22 GDPR)</h3>
        <p>Безплатно можеш да:</p>
        <ul>
          <li><strong>Достъпиш</strong> копие на данните, които пазим за теб.</li>
          <li><strong>Поправиш</strong> неточно записаното.</li>
          <li><strong>Изтриеш</strong> данните си („право да бъдеш забравен“).</li>
          <li><strong>Ограничиш</strong> обработката, докато се решава спор.</li>
          <li><strong>Получиш</strong> данните си в машинно четим формат и да поискаш да ги прехвърлим към друг администратор (преносимост).</li>
          <li><strong>Възразиш</strong> срещу обработка на основание законен интерес или директен маркетинг — по всяко време, без причина.</li>
          <li><strong>Оттеглиш съгласието си</strong> по всяко време — оттеглянето не засяга вече извършена обработка.</li>
        </ul>
        <p>За да упражниш което и да е от тези права, пиши на <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a>. Отговаряме в рамките на един календарен месец (чл. 12, ал. 3 GDPR), а почти винаги — в рамките на няколко дни, защото сме един човек.</p>

        <h3>8. Право на жалба</h3>
        <p>Ако смяташ, че обработваме данните ти неправилно, можеш да подадеш жалба до българския надзорен орган:</p>
        <p><strong>Комисия за защита на личните данни (КЗЛД / CPDP)</strong><br>
        бул. „Проф. Цветан Лазаров“ 2, София 1592<br>
        kzld@cpdp.bg · +359 2 915 3519 · <a href="https://cpdp.bg/" rel="noopener" target="_blank">cpdp.bg</a></p>

        <h3>9. Бисквитки и проследяване</h3>
        <p>Сайтът използва <em>само първостранна функционална памет</em> (малко localStorage, за да помни езика и токена ти за списъка с чакащи). Не използваме рекламни бисквитки от трети страни, fingerprinting или cross-site трекери. PostHog, когато е включен, поставя първостранна бисквитка/localStorage за анонимно отчитане на продуктово ползване на нашия ЕС хост.</p>

        <h3>10. Деца</h3>
        <p>Списъкът е за пълнолетни. Българското законодателство (чл. 25б ЗЗЛД) определя 14 години като възраст на съгласие за услуги на информационното общество. Не събираме съзнателно данни на по-млади лица; ако сте родител и смятате, че сме го направили, пишете ни и ще ги изтрием незабавно.</p>

        <h3>11. Промени в политиката</h3>
        <p>Ако променим нещо съществено, ще (а) обновим датата „Последна редакция“ горе, (б) запазим предишната версия в архива си и (в) уведомим по имейл всички в списъка с чакащи, когато промяната ги касае.</p>

        <h3>12. Контакти</h3>
        <p>Leo Casa Ltd (оперира Vitrina)<br>
        Suite 402, 2 Old Brompton Road, London SW7 3DQ, Обединено кралство<br>
        <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a></p>
      `,
    },
    terms: {
      title: 'Общи условия (затворена бета)',
      body: `
        <p><strong>Последна редакция: 7 май 2026 г.</strong> Vitrina се оперира от <strong>Leo Casa Ltd</strong> (Обединено кралство) — затворен бета режим в София. С присъединяването си към списъка с чакащи приемаш тези условия. Кратки са нарочно.</p>

        <h3>1. Какво представлява</h3>
        <p>Записваш се в опашка. Все още не продаваме на широката публика — в очакване сме на регистрации в БАБХ и КЗП. Записването е безплатно и не те задължава с нищо.</p>

        <h3>2. Комуникации, с които се съгласяваш</h3>
        <p>С попълването на формата и избора на предпочитани канали (WhatsApp, Viber, SMS, телефон, имейл) се съгласяваш да се свързваме с теб по тези канали относно: (а) мястото ти в опашката, (б) първите доставки, които отварят в твоя квартал, и (в) практическа информация (часове за доставка, етапи на регистрацията, бета достъп). Можеш да оттеглиш съгласието си по всяко време — отговор STOP, в разговор, или имейл на <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a> — и спираме в рамките на 48 часа.</p>

        <h3>3. На етапа на записване не се взема плащане</h3>
        <p>Записването е безплатно и никога не иска карта. Пари се движат само ако активно потвърдиш поръчка в бъдеща група, която достигне минимума си.</p>

        <h3>4. Как ще работят бъдещите групови поръчки (преглед, още не е активно)</h3>
        <p>Всяка група има минимален брой поръчки и краен срок. Ако стигнем минимума, картата ти се таксува на цена на едро и поръчката се изпраща. Ако не — слотът се прехвърля, картата ти не се таксува и нищо не се праща.</p>

        <h3>5. Ограничение на отговорността</h3>
        <p>През затворената бета предоставяме списъка „както е“. Не носим отговорност за пропуснати нотификации поради проблеми при оператори или платформи (WhatsApp, Viber, SMS, имейл). Никое от тези условия не намалява задължителните права на потребителите по българското законодателство.</p>

        <h3>6. Приложимо право</h3>
        <p>Тези бета условия се уреждат от правото на Англия и Уелс (юрисдикцията на администратора). Българските потребители запазват всички задължителни защити на българското потребителско право.</p>

        <h3>7. Дружество</h3>
        <p>Leo Casa Ltd · регистрирано в Обединеното кралство · Suite 402, 2 Old Brompton Road, London SW7 3DQ · в очакване на българските регистрации (БАБХ + КЗП) · <a href="mailto:hello@vitrina.bg">hello@vitrina.bg</a></p>
      `,
    },
  },
};;

  function pickLang() {
    var l = (document.documentElement.getAttribute('lang') || 'bg').toLowerCase();
    return l.indexOf('bg') === 0 ? 'bg' : 'en';
  }

  var overlay = null;
  var titleEl = null;
  var bodyEl = null;
  var onKey = null;

  function ensureOverlay() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.id = 'vitrina-legal-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:99999;display:none;align-items:center;' +
      'justify-content:center;padding:24px;background:rgba(14,26,20,0.72);' +
      'font-family:Archivo,system-ui,sans-serif;color:#0e1a14;';

    var card = document.createElement('div');
    card.style.cssText =
      'background:#ffffff;border-radius:18px;max-width:680px;width:100%;' +
      'max-height:88vh;display:flex;flex-direction:column;' +
      'box-shadow:0 24px 60px rgba(0,0,0,0.35);';

    var head = document.createElement('div');
    head.style.cssText =
      'padding:22px 26px 14px;border-bottom:1px solid rgba(28,28,26,0.12);' +
      'display:flex;align-items:center;justify-content:space-between;gap:16px;';

    titleEl = document.createElement('h2');
    titleEl.id = 'vitrina-legal-title';
    titleEl.style.cssText =
      'font-size:22px;font-weight:900;letter-spacing:-0.02em;margin:0;line-height:1.2;';

    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.textContent = '\u00d7';
    closeBtn.style.cssText =
      'background:transparent;border:0;font-size:28px;cursor:pointer;' +
      'color:#0e1a14;line-height:1;padding:4px 8px;';
    closeBtn.addEventListener('click', close);

    head.appendChild(titleEl);
    head.appendChild(closeBtn);

    bodyEl = document.createElement('div');
    bodyEl.id = 'vitrina-legal-body';
    bodyEl.style.cssText =
      'padding:20px 26px 26px;overflow-y:auto;font-size:14px;line-height:1.6;' +
      'color:#1f2a23;';
    var styleTag = document.createElement('style');
    styleTag.textContent =
      '#vitrina-legal-body h3{font-size:15px;font-weight:800;margin:18px 0 8px;letter-spacing:-0.01em}' +
      '#vitrina-legal-body p{margin:0 0 10px}' +
      '#vitrina-legal-body ul{margin:0 0 12px 0;padding-left:22px}' +
      '#vitrina-legal-body li{margin:4px 0}' +
      '#vitrina-legal-body a{color:#c95216;text-decoration:underline}' +
      '#vitrina-legal-body strong{font-weight:700}';
    bodyEl.appendChild(styleTag);

    card.appendChild(head);
    card.appendChild(bodyEl);
    overlay.appendChild(card);
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) close();
    });
    document.body.appendChild(overlay);
  }

  function open(kind) {
    ensureOverlay();
    var lang = pickLang();
    var data = (LEGAL_COPY[lang] && LEGAL_COPY[lang][kind]) || LEGAL_COPY.en[kind];
    if (!data) return;
    titleEl.textContent = data.title;
    // bodyEl's first child is the inline <style> tag; replace everything after it.
    while (bodyEl.childNodes.length > 1) bodyEl.removeChild(bodyEl.lastChild);
    var wrap = document.createElement('div');
    wrap.innerHTML = data.body;
    bodyEl.appendChild(wrap);
    bodyEl.scrollTop = 0;
    overlay.style.display = 'flex';
    if (!onKey) {
      onKey = function (e) { if (e.key === 'Escape') close(); };
      document.addEventListener('keydown', onKey);
    }
  }

  function close() {
    if (overlay) overlay.style.display = 'none';
    if (onKey) {
      document.removeEventListener('keydown', onKey);
      onKey = null;
    }
  }

  global.VitrinaLegal = { open: open, close: close, copy: LEGAL_COPY };
})(window);
