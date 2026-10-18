"""Sayt matnlari: o'zbekcha (asosiy), ruscha, inglizcha (2.9.0). scripts/build_site.py shu yerdan site/*.html yasaydi.

Qoidalar: har uch tilda bir xil kalitlar (build tekshiradi); <b>, <a> kabi belgilar faqat "|safe" bilan chiqadigan
kalitlarda (nomi _html bilan tugaydi). Raqam va da'volar — README / docs dagi o'lchovlarga mos (o'ylab topilmaydi).
"""
from __future__ import annotations

LANGS = ("uz", "ru", "en")

T: dict[str, dict] = {}

# ============================================================================================ o'zbekcha
T["uz"] = {
    "lang_name": "O'zbekcha",
    "title": "Hushyor AI — do'kon kameralari uchun sun'iy intellekt",
    "description": "Hushyor AI mavjud kameralaringizga ulanadi: shubhali harakatni videosi bilan Telegram'ga yuboradi, "
                   "xaridorlarni sanaydi, kassadagi navbatni o'lchaydi va har kuni qisqa hisobot beradi.",
    "og_description": "O'g'irlik signallari, tashriflar, kassa navbati, do'kon oqimi va kunlik hisobot — mavjud kameralar bilan.",
    "skip": "Asosiy qismga o'tish",
    "home_label": "Hushyor AI — bosh sahifa",
    "nav_label": "Sayt bo'limlari",
    "lang_label": "Sayt tili",
    "nav": ["Qanday ishlaydi", "Imkoniyatlar", "Ko'rinishi", "Pilot", "Savollar"],
    "cta_top": "So'rov qoldirish",
    # bosh qism
    "h1": "Kameralaringiz endi o'zi kuzatadi — siz faqat qaror qilasiz",
    "lead": "Do'kon va supermarketlar uchun. Hushyor AI mavjud kameralaringizga ulanadi: shubhali harakatni qisqa "
            "videosi bilan Telegram'ga yuboradi, xaridorlarni sanaydi, kassadagi navbatni o'lchaydi va har kuni ertalab "
            "qisqa hisobot beradi.",
    "cta_main": "Pilot so'rash",
    "cta_second": "Qanday ishlashini ko'rish",
    "facts": ["Yangi kamera shart emas", "Internet uzilsa ham ishlaydi", "Videoarxiv do'koningizda qoladi"],
    "demo_html": "Bu demo server: <a href=\"/login/\">bulut panelini ochish</a> — login va parol kirish oynasida yozilgan.",
    "scene_title": "Kamera ko'rinishi: xaridor polkadan mahsulot olib cho'ntagiga soladi, tizim uning skeletini chizib, "
                   "harakatni qizil ramka bilan belgilaydi",
    "scene_cam": "2-kamera: Savdo zali",
    "scene_risk": "#7 xavf 82",
    "tg_title": "⚠️ Shubhali harakat",
    "tg_where": "Savdo zali, 14:32",
    "tg_why": "Qo'l cho'ntakka kirdi, qo'lda mahsulot",
    "tg_confirm": "✅ Tasdiqlash",
    "tg_confirmed": "✅ Tasdiqlandi",
    "tg_false": "❌ Xato signal",
    "tg_done": "Qo'riqchi 40 soniyada javob berdi",
    "flow": [["Kamera ko'radi", "Mavjud kamera, yangi uskuna shart emas"],
             ["AI xavfni baholaydi", "Skelet, qo'l va buyum bo'yicha, yuz tanilmaydi"],
             ["Telegram'ga klip keladi", "Sabab, rasm va qisqa video bilan"],
             ["Qo'riqchi qaror qiladi", "✅ yoki ❌ — tizim javobdan o'rganadi"]],
    "replay": "Yana ko'rish",
    # tomir tizimi
    "vas_h2": "Qanday ishlaydi: kameradan qarorgacha",
    "vas_sub": "Kameralar — tomirlar, do'kondagi kompyuter — yurak. Video shu yerning o'zida tahlil qilinadi, tashqariga "
               "faqat kerakli xabar chiqadi.",
    "vas_title": "Tizim sxemasi: to'rt kamera do'kon kompyuteriga ulanadi; undan Telegram, do'kon paneli, bulut paneli, "
                 "kunlik hisobot va SMS'ga yo'l ketadi; qo'riqchi javobi Telegram'dan kompyuterga qaytadi",
    "cams": ["Kirish eshigi", "Savdo zali", "Kassa", "Ombor"],
    "cams_short": ["Kirish", "Zal", "Kassa", "Ombor"],
    "heart": ["Do'kon", "kompyuteri"],
    "orbit": "odamni topadi • skeletni chizadi • qo'lni kuzatadi • buyumni qidiradi • qoidalarni tekshiradi • ball beradi • ",
    "outs": ["Telegram", "Do'kon paneli", "Bulut paneli", "Kunlik hisobot", "SMS"],
    "outs_short": ["Telegram", "Panel", "Bulut", "Hisobot", "SMS"],
    "vein": "javob qaytadi — tizim o'rganadi",
    "capillary": "faqat hodisa va raqamlar",
    "vas_items": [
        ["cams", "Kameralar", "Mavjud IP kameralar yoki DVR/NVR ulanadi. Video oqimi do'kon tarmog'idan chiqmaydi."],
        ["heart", "Do'kon kompyuteri — yurak", "Har kadrda odamni topadi va 17 nuqtali skeletini chizadi; qo'l biror "
                  "narsaga uzatilsa — atrofida buyum qidiradi. Odatda tinch paytda soniyasiga 4 kadr, shubhali harakatda — 8."],
        ["tg", "Telegram", "Avval rasm va sabab keladi, keyin klip: shubhali lahzadan 10 soniya oldin va 10 soniya keyin. "
               "Kim nima olishi sozlanadi."],
        ["back", "Javob qaytadi", "Qo'riqchining ✅ yoki ❌ javobi saqlanadi: xato signal ulushi hisobotda ochiq ko'rinadi, "
                 "xulq modeli shu javoblarda qayta o'qitiladi."],
        ["panel", "Do'kon paneli", "Brauzerda: jonli kameralar, hodisalar, tashriflar, navbat, oqim va sozlamalar. "
                  "Internetsiz ham ishlaydi."],
        ["cloud", "Bulut paneli", "Bir nechta filial bitta joyda, rollar bilan. Bulutga faqat hodisa, klip va yig'indi "
                  "raqamlar boradi; internet uzilsa — navbatda kutadi."],
        ["report", "Hisobot va SMS", "Har kuni ertalab kechagi raqamlar va tavsiyalar (PDF ham). Yiqilish yoki tungi "
                   "kirish kabi kritik holatda — SMS."],
    ],
    # imkoniyatlar
    "feat_h2": "Bitta tizim — do'konning olti muammosiga",
    "feat_sub": "Hammasi do'kondagi kompyuterda ishlaydi. Telegram'ga faqat muhim narsa keladi, qolgani — panel va "
                "kunlik hisobotda.",
    "features": [
        ["theft", "O'g'irlik signallari",
         "Qo'l cho'ntakka, kiyim ichiga yoki sumkaga kirdi — sababi va videosi bilan Telegram'ga.",
         ["Polka, kassa va xodim zonalari kamerada chiziladi", "Har signalga ball va sabablar",
          "✅/❌ bir bosishda, tizim javoblardan o'rganadi"]],
        ["visits", "Tashriflar", "Kirish chizig'i bo'yicha: bugun nechta xaridor kirdi va hozir ichkarida nechta.",
         ["Soatlar bo'yicha gavjumlik", "Xodimlar alohida sanaladi", "Kunlar va ish vaqti taqqoslanadi"]],
        ["queue", "Kassa navbati", "Har kassada navbat uzunligi va kutish vaqti, kassir joyida yo'qligi.",
         ["Navbat uzaysa — darhol xabar", "O'rtacha va eng uzoq kutish", "Kassir jadvali bo'yicha tavsiya"]],
        ["flow", "Oqim va heatmap", "Do'kon rejasida: qaysi bo'limga ko'p kirishadi va xaridorlar qanday yuradi.",
         ["Bo'limlar bo'yicha tashriflar", "Eng ko'p yo'nalishlar", "Bo'sh qolayotgan joylar"]],
        ["safety", "Xavfsizlik", "Odam yiqildi, ish vaqtidan tashqari kimdir kirdi, kassa oldida gavjumlik.",
         ["Yiqilish va «odam yotibdi»", "Tungi kirish va to'silgan chiqish", "Kritik holatda SMS"]],
        ["report", "Hisobot va tavsiyalar", "Har kuni ertalab kechagi kun, har dushanba o'tgan hafta — raqamlar va "
         "aniq tavsiyalar.", ["PDF va Telegram'da", "O'tgan davr bilan solishtirish", "Filiallar taqqoslanadi (bulut)"]],
    ],
    "fv_risk": "xavf",
    "fv_in": "bugun kirdi",
    "fv_till": "1-kassa",
    "fv_wait": "kutish",
    "fv_tip": "Shanba 17:00 — 2-kassani oching",
    "fv_days": ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"],
    # kimlar uchun
    "who_h2": "Kimlar uchun",
    "who_sub": "Har do'konda muammo boshqacha — sozlamalar va zonalar shunga qarab tanlanadi.",
    "who": [["Supermarket va minimarket", "Kassa navbati, gavjum soatlar va kichik mahsulot o'g'irligi."],
            ["Kiyim va poyabzal", "Kiyim ichiga yashirish, kiyinish xonasi atrofi, bo'limlar oqimi."],
            ["Dorixona va kosmetika", "Kichik va qimmat mahsulot, kassa oldi, ish vaqtidan tashqari kirish."],
            ["Elektronika va telefonlar", "Vitrina oldida uzoq turish, sumkaga solish, tungi xavfsizlik."],
            ["Do'konlar tarmog'i", "Har filial o'z kompyuterida, egasi hammasini bulut panelida ko'radi."]],
    # ekranlar
    "shots_h2": "Qanday ko'rinadi",
    "shots_sub": "Rasmlar demo do'kondan olingan (raqamlar namuna uchun).",
    "shots_label": "Panel ko'rinishlari",
    "shots": [["Bosh sahifa", "Do'kon paneli bosh sahifasi: bugungi hodisalar, tashriflar, kassa navbati va kameralar",
               "Bugungi holat bir sahifada: signallar, tashriflar, navbat va jonli kameralar."],
              ["Hodisalar", "Hodisalar ro'yxati: har signal rasmi, turi, vaqti va qo'riqchi javobi bilan",
               "Har signal — rasm, video, sabab va kim qachon javob bergani. Tekshirilmaganlar alohida."],
              ["Kassa navbati", "Navbat sahifasi: kassalar hozirgi holati, bugungi kutish vaqti va soatlar bo'yicha grafik",
               "Kassalar hozir: navbatda necha kishi, kassir bormi; bugungi o'rtacha va eng uzoq kutish."],
              ["Oqim (heatmap)", "Do'kon rejasidagi heatmap va bo'limlar bo'yicha tashriflar",
               "Do'kon rejasida gavjum joylar, bo'lim tashriflari va eng ko'p yo'nalish."],
              ["Kunlik hisobot", "Kunlik hisobot: tashriflar, navbat, signallar va tavsiyalar",
               "Har kuni ertalab Telegram'ga: kechagi raqamlar va 2–3 ta aniq tavsiya."],
              ["Filiallar (bulut)", "Bulut paneli: filiallarni taqqoslash jadvali va tavsiyalar",
               "Bir nechta filial bo'lsa — bulut paneli: filiallarni taqqoslash, rollar (egasi, menejer, qorovul)."],
              ["Telefonda", "Bulut paneli telefonda: filiallar holati va tekshirilmagan signallar",
               "Telefonda ham: filiallar holati va tekshirilmagan signallar bir qarashda."]],
    # maxfiylik va talablar
    "trust_h2": "Maxfiylik va talablar",
    "privacy_h3": "Maxfiylik",
    "privacy": ["Yuz tanilmaydi — tizim shaxsni emas, harakatni baholaydi.",
                "Videoarxiv do'kondagi kompyuterda qoladi.",
                "Kim nimani ko'rishi rollar bilan cheklanadi: egasi, menejer, qorovul.",
                "Har signalni odam tekshiradi, xato signal ulushi hisobotda ochiq."],
    "req_h3": "Nima kerak",
    "req": [["Kompyuter", "Windows 10 yoki 11, 8 GB xotira, 4–6 yadroli protsessor (tavsiya). Nechta kamera sig'ishini "
                          "ko'rib chiqishda o'lchab aytamiz."],
            ["Kameralar", "RTSP oqimi beradigan IP kamera yoki DVR/NVR."],
            ["Internet", "Telegram va bulut uchun. Uzilsa, signallar navbatda kutadi va internet qaytganda yetkaziladi."],
            ["Telefon", "Telegram o'rnatilgan har qanday telefon."]],
    # ishga tushirish
    "steps_h2": "Qanday ishga tushadi",
    "steps_sub": "Birinchi uchrashuvdan pilot natijasigacha — odatda bir oy.",
    "steps": [["Ko'rib chiqamiz", "Kameralar, kompyuter va internetni birga ko'ramiz (do'konda yoki video qo'ng'iroqda). "
                                 "Ko'pincha mavjud IP kamera yoki DVR/NVR yetarli."],
              ["O'rnatamiz", "Do'kondagi Windows kompyuterga dastur, kameralarni ulash, zonalarni chizish (polka, kassa, "
                             "eshik). Odatda bir kunda."],
              ["Telegram'ga ulaymiz", "Egasi, menejer va qo'riqchi telefoniga — kim nima olishi sozlanadi."],
              ["4 hafta pilot", "Har hafta raqamlarni birga ko'ramiz, sozlaymiz. Oxirida natija bo'yicha qaror qilasiz."]],
    # pilot
    "pilot_h2": "Pilot — so'z bilan emas, raqam bilan",
    "pilot_sub": "4 haftalik pilot natijasini oldindan kelishilgan mezonlar bilan baholaymiz. Hammasini panelning "
                 "\"Pilot\" bo'limida o'zingiz ko'rib turasiz.",
    "pilot_caption": "Pilot mezonlari",
    "gate": [["Dastur ish vaqtida ishlagan", "≥ 98%"], ["Kameralar ish vaqtida ishlagan", "≥ 95%"],
             ["Xato signal, kamera boshiga kuniga", "≤ 3"], ["Javobsiz qolgan signallar", "≤ 10%"],
             ["Kunlik hisobot o'z vaqtida kelgan kunlar", "≥ 90%"], ["Egasining haftalik bahosi (5 dan)", "≥ 4"]],
    # narx
    "price_h2": "Narx",
    "price_sub": "Narx kameralar soni, filiallar va bulut paneli kerakligiga bog'liq. Pilotdan keyin aniq taklif beramiz.",
    "price_default": "so'rov bo'yicha",
    "price_cta": "Taklif so'rash",
    # savollar
    "faq_h2": "Ko'p beriladigan savollar",
    "faq": [["Yangi kamera sotib olish kerakmi?",
             "Ko'pincha yo'q: RTSP oqimi beradigan IP kameralar yoki DVR/NVR ulanadi. Ko'rib chiqishda qaysi kamera qaysi "
             "ish uchun yaroqli ekanini aytamiz (masalan, kassa navbati uchun kassani yuqoridan ko'radigan kamera yaxshi)."],
            ["Internet uzilsa nima bo'ladi?",
             "Tahlil do'kondagi kompyuterda bo'ladi — internet uzilsa ham kuzatuv davom etadi. Signallar va raqamlar "
             "internet qaytganda Telegram va bulutga yetkaziladi."],
            ["Videolar qayerda saqlanadi?",
             "Videoarxiv do'koningizdagi kompyuterda. Telegram'ga faqat signalning qisqa klipi va rasmi boradi; bulut "
             "paneli ishlatilsa, unga ham signal kliplari va yig'indi raqamlar boradi — doimiy video oqimi emas."],
            ["Tizim xato qiladimi?",
             "Ha, har qanday kamera tizimi kabi. Shuning uchun har signalni odam tekshiradi, xato signal ulushi hisobotda "
             "ochiq ko'rsatiladi va zonalar, sezgirlik shu raqamlar asosida sozlanadi."],
            ["Odamlarning yuzini taniydimi?",
             "Yo'q. Tizim shaxsni aniqlamaydi — harakatni baholaydi: qo'l, sumka, polka oldida turish, navbat, o'tish chizig'i."],
            ["Bir nechta filialim bor — hammasini bir joyda ko'ramanmi?",
             "Ha: har filialdagi kompyuter bulut paneliga ulanadi. Egasi hamma filiallarni, menejer va qo'riqchi faqat o'z "
             "filialini ko'radi. Har dushanba filiallar taqqoslangan xulosa keladi."],
            ["Kompyuter o'chib qolsa-chi?",
             "Dastur Windows bilan birga o'zi ishga tushadi va qotib qolsa qayta yoqiladi. Kamera uzilsa — Telegram'ga xabar "
             "keladi; filial internetdan uzilsa — bulut panelida ko'rinadi."],
            ["Panel va bot qaysi tilda?",
             "Hozircha o'zbek tilida (lotin yozuvi). Rus yoki ingliz tili kerak bo'lsa, so'rovda yozing."]],
    # aloqa
    "contact_h2": "So'rov qoldiring",
    "contact_sub": "Telefon raqamingizni qoldiring — bog'lanib, do'koningizni ko'rib chiqish vaqtini kelishamiz.",
    "f_name": "Ismingiz *",
    "f_phone": "Telefon *",
    "f_store": "Do'kon nomi",
    "f_city": "Shahar",
    "f_branches": "Nechta filial / do'kon",
    "f_choose": "Tanlang",
    "f_branch_opts": ["1", "2–3", "4–10", "10 dan ko'p"],
    "f_message": "Izoh",
    "f_message_ph": "Masalan: 6 ta kamera bor, kassada navbat ko'p",
    "f_hp": "Bu maydonni bo'sh qoldiring",
    "f_submit": "So'rov yuborish",
    "f_fine": "Raqamingiz faqat siz bilan bog'lanish uchun ishlatiladi.",
    "foot_login": "Mijozlar uchun: panelga kirish",
    # skript xabarlari (app.js)
    "js": {"name": "Ismingizni yozing", "phone": "Telefon raqamini to'liq yozing: +998 90 123 45 67",
           "sending": "Yuborilmoqda…", "thanks": "Rahmat! So'rovingiz qabul qilindi — tez orada bog'lanamiz.",
           "fail": "Yuborib bo'lmadi — birozdan keyin qayta urinib ko'ring.", "busy": "",
           "expired": "", "phone_bad": "",
           "net": "Internet bilan muammo — qayta urinib ko'ring", "call": "yoki qo'ng'iroq qiling:",
           "k_phone": "Telefon", "k_tg": "Telegram", "k_email": "Email", "k_addr": "Manzil", "version": "versiya"},
}

# ============================================================================================ ruscha
T["ru"] = {
    "lang_name": "Русский",
    "title": "Hushyor AI — искусственный интеллект для камер магазина",
    "description": "Hushyor AI подключается к вашим камерам: сообщает в Telegram о подозрительных действиях и присылает "
                   "видео, считает покупателей, измеряет очередь на кассе и каждый день отправляет короткий отчёт.",
    "og_description": "Сигналы о кражах, посещаемость, очереди на кассах, потоки по магазину и ежедневный отчёт — на ваших камерах.",
    "skip": "Перейти к содержанию",
    "home_label": "Hushyor AI — главная",
    "nav_label": "Разделы сайта",
    "lang_label": "Язык сайта",
    "nav": ["Как работает", "Возможности", "Интерфейс", "Пилот", "Вопросы"],
    "cta_top": "Оставить заявку",
    "h1": "Ваши камеры следят сами — вам остаётся только принять решение",
    "lead": "Для магазинов и супермаркетов. Hushyor AI подключается к вашим камерам: сообщает в Telegram о подозрительных "
            "действиях и присылает короткое видео, считает покупателей, измеряет очередь на кассе и каждое утро "
            "отправляет короткий отчёт.",
    "cta_main": "Запросить пилот",
    "cta_second": "Как это работает",
    "facts": ["Новые камеры не нужны", "Работает при обрыве интернета", "Видеоархив остаётся в магазине"],
    "demo_html": "Это демо-сервер: <a href=\"/login/\">открыть облачную панель</a> — логин и пароль указаны на странице входа.",
    "scene_title": "Вид с камеры: покупатель берёт товар с полки и кладёт в карман, система строит его скелет и выделяет "
                   "действие красной рамкой",
    "scene_cam": "Камера 2: Торговый зал",
    "scene_risk": "#7 риск 82",
    "tg_title": "⚠️ Подозрительное действие",
    "tg_where": "Торговый зал, 14:32",
    "tg_why": "Рука в кармане, в руке товар",
    "tg_confirm": "✅ Подтвердить",
    "tg_confirmed": "✅ Подтверждено",
    "tg_false": "❌ Ложная тревога",
    "tg_done": "Охранник ответил за 40 секунд",
    "flow": [["Камера видит", "Существующая камера, новое оборудование не нужно"],
             ["ИИ оценивает риск", "По скелету, рукам и товару — лица не распознаются"],
             ["Клип приходит в Telegram", "С причиной, фото и коротким видео"],
             ["Охранник решает", "✅ или ❌ — система учится на ответах"]],
    "replay": "Смотреть снова",
    "vas_h2": "Как это работает: от камеры до решения",
    "vas_sub": "Камеры — сосуды, компьютер в магазине — сердце. Видео анализируется прямо на месте, наружу уходит только "
               "нужное сообщение.",
    "vas_title": "Схема системы: четыре камеры подключены к компьютеру магазина; от него идут пути в Telegram, панель "
                 "магазина, облачную панель, ежедневный отчёт и SMS; ответ охранника возвращается из Telegram в компьютер",
    "cams": ["Вход", "Торговый зал", "Касса", "Склад"],
    "cams_short": ["Вход", "Зал", "Касса", "Склад"],
    "heart": ["Компьютер", "магазина"],
    "orbit": "находит людей • строит скелет • следит за руками • ищет товар • проверяет правила • считает балл • ",
    "outs": ["Telegram", "Панель магазина", "Облачная панель", "Ежедневный отчёт", "SMS"],
    "outs_short": ["Telegram", "Панель", "Облако", "Отчёт", "SMS"],
    "vein": "ответ возвращается — система учится",
    "capillary": "только события и цифры",
    "vas_items": [
        ["cams", "Камеры", "Подключаются существующие IP-камеры или регистратор (DVR/NVR). Видеопоток не покидает сеть магазина."],
        ["heart", "Компьютер магазина — сердце", "В каждом кадре находит человека и строит скелет из 17 точек; когда рука "
                  "тянется к товару, ищет предметы рядом с ней. По умолчанию — 4 кадра в секунду в спокойное время и 8 при "
                  "подозрительном движении."],
        ["tg", "Telegram", "Сначала приходят фото и причина, затем клип: 10 секунд до подозрительного момента и 10 после. "
               "Кто что получает — настраивается."],
        ["back", "Ответ возвращается", "Ответ охранника ✅ или ❌ сохраняется: доля ложных тревог открыто видна в отчёте, "
                 "а модель поведения дообучается на этих ответах."],
        ["panel", "Панель магазина", "В браузере: живые камеры, события, посещаемость, очереди, потоки и настройки. "
                  "Работает и без интернета."],
        ["cloud", "Облачная панель", "Все филиалы в одном месте, с ролями. В облако уходят только события, клипы и сводные "
                  "цифры; без интернета они ждут в очереди."],
        ["report", "Отчёт и SMS", "Каждое утро — вчерашние цифры и рекомендации (в том числе PDF). В критических случаях, "
                   "например при падении или ночном проникновении, — SMS."],
    ],
    "feat_h2": "Одна система — шесть задач магазина",
    "feat_sub": "Всё работает на компьютере в магазине. В Telegram приходит только важное, остальное — в панели и "
                "ежедневном отчёте.",
    "features": [
        ["theft", "Сигналы о кражах", "Рука в кармане, под одеждой или в сумке — в Telegram с причиной и видео.",
         ["Зоны полок, кассы и персонала размечаются на камере", "У каждого сигнала — балл и причины",
          "✅/❌ в одно касание, система учится на ответах"]],
        ["visits", "Посещаемость", "По линии у входа: сколько покупателей пришло сегодня и сколько сейчас внутри.",
         ["Загрузка по часам", "Сотрудники считаются отдельно", "Сравнение по дням и рабочим часам"]],
        ["queue", "Очередь на кассе", "Длина очереди и время ожидания на каждой кассе, отсутствие кассира.",
         ["Очередь растёт — сразу сообщение", "Среднее и самое долгое ожидание", "Рекомендации по графику кассиров"]],
        ["flow", "Потоки и тепловая карта", "На плане магазина: в какие отделы чаще заходят и как движутся покупатели.",
         ["Посещения по отделам", "Самые частые маршруты", "Места, которые пустуют"]],
        ["safety", "Безопасность", "Человек упал, кто-то вошёл в нерабочее время, толпа у кассы.",
         ["Падение и «человек лежит»", "Ночное проникновение и заблокированный выход", "SMS в критических случаях"]],
        ["report", "Отчёты и рекомендации", "Каждое утро — вчерашний день, каждый понедельник — прошлая неделя: цифры и "
         "конкретные советы.", ["В PDF и в Telegram", "Сравнение с прошлым периодом", "Сравнение филиалов (облако)"]],
    ],
    "fv_risk": "риск",
    "fv_in": "вошли сегодня",
    "fv_till": "Касса 1",
    "fv_wait": "ожидание",
    "fv_tip": "Суббота 17:00 — откройте кассу 2",
    "fv_days": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    "who_h2": "Для кого",
    "who_sub": "У каждого магазина свои проблемы — настройки и зоны подбираются под них.",
    "who": [["Супермаркеты и минимаркеты", "Очереди на кассах, часы пик и кражи мелкого товара."],
            ["Одежда и обувь", "Сокрытие товара под одеждой, зона примерочных, движение по отделам."],
            ["Аптеки и косметика", "Мелкий дорогой товар, прикассовая зона, проникновение в нерабочее время."],
            ["Электроника и телефоны", "Долгое стояние у витрины, товар, убранный в сумку, безопасность ночью."],
            ["Сети магазинов", "Каждый филиал работает на своём компьютере, владелец видит всё в облачной панели."]],
    "shots_h2": "Как это выглядит",
    "shots_sub": "Снимки из демо-магазина (панель на узбекском языке, цифры — пример).",
    "shots_label": "Экраны панели",
    "shots": [["Главная", "Главная страница панели магазина: события за сегодня, посещаемость, очередь и камеры",
               "Состояние за сегодня на одной странице: сигналы, посещаемость, очереди и живые камеры."],
              ["События", "Список событий: фото, тип, время и ответ охранника по каждому сигналу",
               "По каждому сигналу — фото, видео, причина и кто и когда ответил. Непроверенные — отдельно."],
              ["Очередь", "Страница очередей: состояние касс сейчас, ожидание за сегодня и график по часам",
               "Кассы сейчас: сколько человек в очереди, есть ли кассир; среднее и самое долгое ожидание."],
              ["Потоки (карта)", "Тепловая карта на плане магазина и посещения по отделам",
               "Загруженные места на плане, посещения отделов и самый частый маршрут."],
              ["Ежедневный отчёт", "Ежедневный отчёт: посещаемость, очереди, сигналы и рекомендации",
               "Каждое утро в Telegram: вчерашние цифры и 2–3 конкретные рекомендации."],
              ["Филиалы (облако)", "Облачная панель: таблица сравнения филиалов и рекомендации",
               "Если филиалов несколько — облачная панель: сравнение филиалов, роли (владелец, менеджер, охранник)."],
              ["На телефоне", "Облачная панель на телефоне: состояние филиалов и непроверенные сигналы",
               "И на телефоне: состояние филиалов и непроверенные сигналы с одного взгляда."]],
    "trust_h2": "Конфиденциальность и требования",
    "privacy_h3": "Конфиденциальность",
    "privacy": ["Лица не распознаются — система оценивает действия, а не личность.",
                "Видеоархив остаётся на компьютере в магазине.",
                "Доступ ограничен ролями: владелец, менеджер, охранник.",
                "Каждый сигнал проверяет человек, доля ложных тревог открыто показана в отчёте."],
    "req_h3": "Что нужно",
    "req": [["Компьютер", "Windows 10 или 11, 8 ГБ памяти, 4–6-ядерный процессор (рекомендация). Сколько камер он "
                          "потянет, измерим на осмотре."],
            ["Камеры", "IP-камеры с RTSP-потоком или регистратор DVR/NVR."],
            ["Интернет", "Нужен для Telegram и облака. При обрыве сигналы ждут в очереди и доставляются, когда связь вернётся."],
            ["Телефон", "Любой телефон с Telegram."]],
    "steps_h2": "Как проходит запуск",
    "steps_sub": "От первой встречи до итогов пилота — обычно месяц.",
    "steps": [["Осматриваем", "Вместе смотрим камеры, компьютер и интернет (в магазине или по видеосвязи). Чаще всего "
                              "хватает имеющихся IP-камер или регистратора."],
              ["Устанавливаем", "Установка программы на компьютер магазина, подключение камер, разметка зон (полки, касса, "
                                "вход). Обычно за один день."],
              ["Подключаем Telegram", "На телефоны владельца, менеджера и охранника — кто что получает, настраивается."],
              ["4 недели пилота", "Каждую неделю вместе смотрим цифры и настраиваем. В конце вы решаете по результатам."]],
    "pilot_h2": "Пилот — цифры, а не обещания",
    "pilot_sub": "Итоги 4-недельного пилота оцениваем по заранее согласованным критериям. Всё видно в разделе «Пилот» панели.",
    "pilot_caption": "Критерии пилота",
    "gate": [["Программа работала в рабочее время", "≥ 98%"], ["Камеры работали в рабочее время", "≥ 95%"],
             ["Ложных сигналов на камеру в день", "≤ 3"], ["Сигналов без ответа", "≤ 10%"],
             ["Дней, когда отчёт пришёл вовремя", "≥ 90%"], ["Недельная оценка владельца (из 5)", "≥ 4"]],
    "price_h2": "Цена",
    "price_sub": "Цена зависит от числа камер, филиалов и того, нужна ли облачная панель. Точное предложение — после пилота.",
    "price_default": "по запросу",
    "price_cta": "Запросить предложение",
    "faq_h2": "Частые вопросы",
    "faq": [["Нужно ли покупать новые камеры?",
             "Чаще всего нет: подключаются IP-камеры с RTSP-потоком или регистратор DVR/NVR. На осмотре скажем, какая "
             "камера для какой задачи подходит (например, для очереди лучше камера, которая видит кассу сверху)."],
            ["Что будет, если пропадёт интернет?",
             "Анализ идёт на компьютере в магазине — наблюдение продолжается и без интернета. Сигналы и цифры доставляются "
             "в Telegram и облако, когда связь вернётся."],
            ["Где хранится видео?",
             "Видеоархив — на компьютере в вашем магазине. В Telegram уходит только короткий клип и фото сигнала; если "
             "используется облачная панель, туда тоже идут клипы сигналов и сводные цифры — не постоянный видеопоток."],
            ["Ошибается ли система?",
             "Да, как любая система видеонаблюдения. Поэтому каждый сигнал проверяет человек, доля ложных тревог открыто "
             "показана в отчёте, а зоны и чувствительность настраиваются по этим цифрам."],
            ["Распознаёт ли система лица?",
             "Нет. Система не определяет личность — она оценивает действия: руки, сумку, стояние у полки, очередь, "
             "пересечение линии."],
            ["У меня несколько филиалов — увижу всё в одном месте?",
             "Да: компьютер каждого филиала подключается к облачной панели. Владелец видит все филиалы, менеджер и "
             "охранник — только свой. Каждый понедельник приходит сводка со сравнением филиалов."],
            ["А если компьютер выключится?",
             "Программа запускается вместе с Windows и перезапускается, если зависла. Если отключилась камера — придёт "
             "сообщение в Telegram; если филиал пропал из сети — это видно в облачной панели."],
            ["На каком языке панель и бот?",
             "Пока на узбекском (латиница). Если нужен русский или английский — напишите об этом в заявке."]],
    "contact_h2": "Оставьте заявку",
    "contact_sub": "Оставьте номер телефона — мы свяжемся и договоримся об осмотре магазина.",
    "f_name": "Ваше имя *",
    "f_phone": "Телефон *",
    "f_store": "Название магазина",
    "f_city": "Город",
    "f_branches": "Сколько филиалов / магазинов",
    "f_choose": "Выберите",
    "f_branch_opts": ["1", "2–3", "4–10", "больше 10"],
    "f_message": "Комментарий",
    "f_message_ph": "Например: 6 камер, на кассе большая очередь",
    "f_hp": "Оставьте это поле пустым",
    "f_submit": "Отправить заявку",
    "f_fine": "Номер используется только для связи с вами.",
    "foot_login": "Для клиентов: вход в панель",
    "js": {"name": "Укажите имя", "phone": "Укажите номер полностью: +998 90 123 45 67",
           "sending": "Отправляем…", "thanks": "Спасибо! Заявка принята — скоро свяжемся.",
           "fail": "Не удалось отправить — попробуйте чуть позже.",
           "busy": "Сейчас слишком много заявок — позвоните или попробуйте позже.",
           "expired": "Форма устарела — обновите страницу и отправьте заново.",
           "phone_bad": "Проверьте номер: +998 XX XXX XX XX",
           "net": "Проблема с интернетом — попробуйте ещё раз", "call": "или позвоните:",
           "k_phone": "Телефон", "k_tg": "Telegram", "k_email": "Эл. почта", "k_addr": "Адрес", "version": "версия"},
}

# ============================================================================================ inglizcha
T["en"] = {
    "lang_name": "English",
    "title": "Hushyor AI — AI for store cameras",
    "description": "Hushyor AI connects to your existing cameras: it sends suspicious moments to Telegram with video, counts "
                   "visitors, measures checkout queues and sends a short report every day.",
    "og_description": "Theft alerts, visitor counts, checkout queues, in-store flow and a daily report — on the cameras you already have.",
    "skip": "Skip to content",
    "home_label": "Hushyor AI — home",
    "nav_label": "Site sections",
    "lang_label": "Site language",
    "nav": ["How it works", "Features", "Screens", "Pilot", "FAQ"],
    "cta_top": "Get in touch",
    "h1": "Your cameras watch for you — you just make the call",
    "lead": "For shops and supermarkets. Hushyor AI connects to the cameras you already have: it sends suspicious moments "
            "to Telegram with a short clip, counts visitors, measures checkout queues and sends a short report every morning.",
    "cta_main": "Request a pilot",
    "cta_second": "See how it works",
    "facts": ["No new cameras needed", "Keeps working when the internet drops", "Your video archive stays in the store"],
    "demo_html": "This is a demo server: <a href=\"/login/\">open the cloud dashboard</a> — the login and password are shown "
                 "on the sign-in page.",
    "scene_title": "Camera view: a shopper takes an item from the shelf and puts it in a pocket; the system draws their pose "
                   "and marks the action with a red box",
    "scene_cam": "Camera 2: Sales floor",
    "scene_risk": "#7 risk 82",
    "tg_title": "⚠️ Suspicious activity",
    "tg_where": "Sales floor, 14:32",
    "tg_why": "Hand into pocket, item in hand",
    "tg_confirm": "✅ Confirm",
    "tg_confirmed": "✅ Confirmed",
    "tg_false": "❌ False alarm",
    "tg_done": "The guard replied in 40 seconds",
    "flow": [["The camera sees", "Your existing camera, no new hardware"],
             ["AI scores the risk", "From pose, hands and items — no face recognition"],
             ["A clip lands in Telegram", "With the reason, a photo and a short video"],
             ["The guard decides", "✅ or ❌ — the system learns from each answer"]],
    "replay": "Watch again",
    "vas_h2": "How it works: from camera to decision",
    "vas_sub": "Cameras are the vessels, the in-store computer is the heart. Video is analysed on site; only the message "
               "that matters leaves the building.",
    "vas_title": "System diagram: four cameras feed the in-store computer; paths lead from it to Telegram, the store "
                 "dashboard, the cloud dashboard, the daily report and SMS; the guard's answer flows back from Telegram to "
                 "the computer",
    "cams": ["Entrance", "Sales floor", "Checkout", "Stockroom"],
    "cams_short": ["Entrance", "Floor", "Till", "Stock"],
    "heart": ["In-store", "computer"],
    "orbit": "finds people • tracks pose • follows hands • spots items • checks rules • scores risk • ",
    "outs": ["Telegram", "Store dashboard", "Cloud dashboard", "Daily report", "SMS"],
    "outs_short": ["Telegram", "Dashboard", "Cloud", "Report", "SMS"],
    "vein": "answers flow back — the system learns",
    "capillary": "events and numbers only",
    "vas_items": [
        ["cams", "Cameras", "Your existing IP cameras or DVR/NVR are connected. The video stream never leaves the store network."],
        ["heart", "In-store computer — the heart", "In every frame it finds people and maps a 17-point skeleton; when a "
                  "hand reaches for something, it also looks for items around it. By default 4 frames a second when things "
                  "are calm, 8 when something looks suspicious."],
        ["tg", "Telegram", "A photo and the reason arrive first, then a clip covering 10 seconds before and after the "
               "moment. You choose who gets what."],
        ["back", "The answer flows back", "The guard's ✅ or ❌ is stored: the false-alarm rate is shown openly in reports "
                 "and the behaviour model is retrained on these answers."],
        ["panel", "Store dashboard", "In the browser: live cameras, events, visitors, queues, flow and settings. "
                  "Works without the internet too."],
        ["cloud", "Cloud dashboard", "All branches in one place, with roles. Only events, clips and totals go to the "
                  "cloud; if the internet drops, they wait in a queue."],
        ["report", "Reports and SMS", "Every morning: yesterday's numbers and recommendations (PDF too). For critical "
                   "events such as a fall or a night-time entry — an SMS."],
    ],
    "feat_h2": "One system for six store problems",
    "feat_sub": "Everything runs on the computer in your store. Only what matters reaches Telegram; the rest lives in the "
                "dashboard and the daily report.",
    "features": [
        ["theft", "Theft alerts", "A hand goes into a pocket, under clothing or into a bag — sent to Telegram with the "
         "reason and a clip.", ["Shelf, till and staff zones are drawn on each camera", "Every alert has a score and reasons",
                                "✅/❌ in one tap; the system learns from answers"]],
        ["visits", "Visitors", "Using a counting line at the entrance: how many shoppers came in today and how many are "
         "inside now.",
         ["Busy hours at a glance", "Staff counted separately", "Days and opening hours compared"]],
        ["queue", "Checkout queues", "Queue length and waiting time at every till, and when a cashier is missing.",
         ["A message as soon as a queue builds up", "Average and longest wait", "Suggestions for the cashier schedule"]],
        ["flow", "Flow and heatmap", "On your floor plan: which sections draw people in and how shoppers move.",
         ["Visits by section", "Most common routes", "Areas that stay empty"]],
        ["safety", "Safety", "Someone fell, someone came in after hours, a crowd at the tills.",
         ["Falls and “person lying down”", "Night entry and blocked exits", "SMS for critical events"]],
        ["report", "Reports and recommendations", "Every morning for yesterday, every Monday for last week: numbers and "
         "specific advice.", ["As PDF and in Telegram", "Compared with the previous period", "Branches side by side (cloud)"]],
    ],
    "fv_risk": "risk",
    "fv_in": "in today",
    "fv_till": "Till 1",
    "fv_wait": "wait",
    "fv_tip": "Saturday 5 pm — open till 2",
    "fv_days": ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
    "who_h2": "Who it's for",
    "who_sub": "Every store has different problems — settings and zones are chosen to match.",
    "who": [["Supermarkets and convenience stores", "Checkout queues, peak hours and small-item theft."],
            ["Clothing and footwear", "Items hidden under clothing, fitting-room areas, movement between sections."],
            ["Pharmacies and cosmetics", "Small, valuable items, the till area and after-hours entry."],
            ["Electronics and phone shops", "Lingering at display cases, items going into bags, night-time security."],
            ["Retail chains", "Each branch runs on its own computer; the owner sees them all in the cloud dashboard."]],
    "shots_h2": "What it looks like",
    "shots_sub": "Screenshots from the demo store (the dashboard is in Uzbek; the numbers are sample data).",
    "shots_label": "Dashboard screens",
    "shots": [["Home", "Store dashboard home: today's events, visitors, checkout queues and cameras",
               "Today at a glance: alerts, visitors, queues and live cameras."],
              ["Events", "Event list: a photo, type, time and the guard's answer for every alert",
               "Every alert with photo, video, reason and who answered when. Unreviewed ones kept separate."],
              ["Queues", "Queue page: the tills right now, today's waiting time and an hourly chart",
               "The tills right now: people in line, whether a cashier is there; average and longest wait today."],
              ["Flow (heatmap)", "Heatmap on the store plan and visits by section",
               "Busy spots on your floor plan, section visits and the most common route."],
              ["Daily report", "Daily report: visitors, queues, alerts and recommendations",
               "Every morning in Telegram: yesterday's numbers and 2–3 specific recommendations."],
              ["Branches (cloud)", "Cloud dashboard: branch comparison table and recommendations",
               "With several branches — the cloud dashboard: branches compared, roles for owner, manager and guard."],
              ["On the phone", "Cloud dashboard on a phone: branch status and unreviewed alerts",
               "On the phone too: branch status and unreviewed alerts at a glance."]],
    "trust_h2": "Privacy and requirements",
    "privacy_h3": "Privacy",
    "privacy": ["No face recognition — the system judges actions, not identities.",
                "The video archive stays on the computer in your store.",
                "Access is limited by role: owner, manager, guard.",
                "A person checks every alert; the false-alarm rate is shown openly in reports."],
    "req_h3": "What you need",
    "req": [["Computer", "Windows 10 or 11, 8 GB RAM, a 4–6-core processor (recommended). We measure how many cameras it "
                         "can handle during the site visit."],
            ["Cameras", "IP cameras with an RTSP stream, or a DVR/NVR."],
            ["Internet", "Needed for Telegram and the cloud. If it drops, alerts queue up and are delivered once it's back."],
            ["Phone", "Any phone with Telegram."]],
    "steps_h2": "How we get started",
    "steps_sub": "From the first meeting to pilot results — usually about a month.",
    "steps": [["We assess", "We look at the cameras, computer and internet together (in store or on a video call). "
                            "Existing IP cameras or a DVR/NVR are usually enough."],
              ["We install", "Software on the store computer, cameras connected, zones drawn (shelves, till, entrance). "
                             "Usually within a day."],
              ["We connect Telegram", "To the owner's, manager's and guard's phones — you choose who gets what."],
              ["A 4-week pilot", "Every week we review the numbers together and fine-tune. At the end, you decide based on "
                                 "the results."]],
    "pilot_h2": "A pilot judged by numbers, not promises",
    "pilot_sub": "We judge the 4-week pilot by criteria agreed in advance. You can follow everything in the dashboard's "
                 "Pilot section.",
    "pilot_caption": "Pilot criteria",
    "gate": [["Software running during opening hours", "≥ 98%"], ["Cameras running during opening hours", "≥ 95%"],
             ["False alerts per camera per day", "≤ 3"], ["Alerts left unanswered", "≤ 10%"],
             ["Days the daily report arrived on time", "≥ 90%"], ["Owner's weekly rating (out of 5)", "≥ 4"]],
    "price_h2": "Pricing",
    "price_sub": "Pricing depends on the number of cameras, branches and whether you need the cloud dashboard. We make an "
                 "exact offer after the pilot.",
    "price_default": "on request",
    "price_cta": "Request an offer",
    "faq_h2": "Frequently asked questions",
    "faq": [["Do I need to buy new cameras?",
             "Usually not: IP cameras with an RTSP stream or a DVR/NVR can be connected. During the visit we tell you which "
             "camera suits which job (for queues, for example, a camera that sees the till from above works best)."],
            ["What happens if the internet goes down?",
             "Analysis runs on the computer in your store, so monitoring continues without the internet. Alerts and numbers "
             "reach Telegram and the cloud once the connection is back."],
            ["Where is the video stored?",
             "The video archive stays on the computer in your store. Only a short clip and a photo of each alert go to "
             "Telegram; if you use the cloud dashboard, alert clips and totals go there too — never a continuous stream."],
            ["Does the system make mistakes?",
             "Yes, like any camera system. That's why a person checks every alert, the false-alarm rate is shown openly in "
             "reports, and zones and sensitivity are tuned from those numbers."],
            ["Does it recognise faces?",
             "No. The system does not identify anyone — it judges actions: hands, bags, lingering at a shelf, queues, "
             "crossing a line."],
            ["I have several branches — can I see them all in one place?",
             "Yes: the computer in each branch connects to the cloud dashboard. The owner sees every branch; managers and "
             "guards see only their own. Every Monday a summary comparing branches arrives."],
            ["What if the computer goes off?",
             "The software starts with Windows and restarts itself if it hangs. If a camera goes offline you get a Telegram "
             "message; if a branch drops off the network, the cloud dashboard shows it."],
            ["What language are the dashboard and bot in?",
             "Uzbek (Latin script) for now. If you need Russian or English, mention it in your request."]],
    "contact_h2": "Leave a request",
    "contact_sub": "Leave your phone number — we'll get in touch to arrange a look at your store.",
    "f_name": "Your name *",
    "f_phone": "Phone *",
    "f_store": "Store name",
    "f_city": "City",
    "f_branches": "Number of branches / stores",
    "f_choose": "Choose",
    "f_branch_opts": ["1", "2–3", "4–10", "more than 10"],
    "f_message": "Comment",
    "f_message_ph": "For example: 6 cameras, long queues at the till",
    "f_hp": "Leave this field empty",
    "f_submit": "Send request",
    "f_fine": "We only use your number to contact you.",
    "foot_login": "For customers: sign in to the dashboard",
    "js": {"name": "Enter your name", "phone": "Enter the full phone number: +998 90 123 45 67",
           "sending": "Sending…", "thanks": "Thank you! Your request has been received — we'll be in touch soon.",
           "fail": "Couldn't send — please try again a little later.",
           "busy": "Too many requests right now — please call or try again later.",
           "expired": "The form has expired — reload the page and send it again.",
           "phone_bad": "Check the number: +998 XX XXX XX XX",
           "net": "Connection problem — please try again", "call": "or call:",
           "k_phone": "Phone", "k_tg": "Telegram", "k_email": "Email", "k_addr": "Address", "version": "version"},
}

# Forma "filiallar soni" qiymati serverga (va egasiga Telegram xabarida) har doim o'zbekcha boradi
BRANCH_VALUES = ["1", "2–3", "4–10", "10 dan ko'p"]
