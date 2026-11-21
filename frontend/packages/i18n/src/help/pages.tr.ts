import type { HelpSlug } from "./index";
import type { HelpDocument } from "./types";

/** The help pages in tr: a page not here shows in English. */
export const helpTr: Partial<Record<HelpSlug, HelpDocument>> = {
  "run-on-your-computer": {
  "title": "VortoOne'i kendi bilgisayarınızda çalıştırın",
  "summary": "Güncel VortoOne Desktop sürümünün kurulum paketini ve bağlantı talimatlarını kullanın. Bu sürüm barındırılan bulut hizmeti veya tek komutla sunucu kurulumu sunmaz.",
  "sections": []
},
  computers: {
    title: "Bilgisayarlarınız ve telefonlarınız",
    summary:
      "Bir Mac, PC, Linux bilgisayar, sunucu ya da telefon getirin, ajanlarınız onun üzerinde çalışsın. Hem VortoOne Cloud'da hem kendi sunucunuzda.",
    sections: [
      {
        id: "app",
        heading: "VortoOne uygulamasından",
        blocks: [
          "Uygulamayı bilgisayara kurun, açın ve çalışma alanınızda oturum açın. Uygulama **<workspace> ajanları bu Mac cihazını kullanabilsin mi?** diye sorar (Windows'ta bu PC, Linux'ta bu bilgisayar). Kimlerin kullanabileceğini seçin ve **İzin ver** düğmesine basın. Hepsi bu.",
          "Zaten oturum açtınız mı? **Tercihler\u00a0›\u00a0Genel** bölümünü açın ve ilk karttaki çalışma alanı satırında **İzin ver** düğmesine basın. Orada yok mu? O kartın en altındaki **Çalışma alanı ekle…** düğmesine basın ve oturum açın.",
        ],
      },
      {
        id: "web",
        heading: "Web sitesinden",
        blocks: [
          "**Ayarlar\u00a0›\u00a0Cihazlar** bölümünde **Cihaz bağla** düğmesine basın:",
          {
            list: [
              "**Bu Mac** ya da **Bu PC**: uygulamayı açar, uygulama önce size sorar.",
              "**Başka bir bilgisayar**: uygulamayı oraya kurun, kendi hesabınızla oturum açın ve **İzin ver** düğmesine basın. Bilgisayar burada kendiliğinden görünür.",
              "**Bir sunucu ya da VPS**: terminalinde tek bir satır çalıştırın, sonra katılmasına izin verin. Satır yalnızca bir kez ve 10 dakika boyunca çalışır.",
            ],
          },
          "Uygulama tek tıkla yalnızca https kullanan bir adrese bağlanır, örneğin `.ts.net` ile biten bir Tailscale adresine. Düz bir http adresinde terminal satırını kullanın.",
        ],
      },
      {
        id: "phones",
        heading: "Telefonlar",
        blocks: [
          "Bir Android telefon, zaten bağlı herhangi bir bilgisayar üzerinden bağlanır: kabloyla ya da Mayıs 2026 güvenlik güncellemesi yüklü Android 14 ve sonrasında Wi-Fi ile. Bir iPhone, uygulama ve Xcode kurulu bir Mac üzerinden bağlanır, gerisini uygulama hazırlar. İkisi de **Cihaz bağla** ile başlar.",
        ],
      },
      {
        id: "who",
        heading: "Kimler kullanabilir",
        blocks: [
          "Yeni bir cihaz yalnızca sizindir: **Yalnızca ben**, siz ve sizin için çalışan ajanlar demektir. İzin verirken ya da daha sonra **Ayarlar\u00a0›\u00a0Cihazlar** içindeki sayfasından onu çalışma alanındaki herkese ya da bazı kişilere ve rollere açın.",
        ],
      },
      {
        id: "open",
        heading: "Uygulama açıkken",
        blocks: [
          "Ajanlar bir bilgisayarda yalnızca uygulama orada çalışırken ve bilgisayar uyanıkken çalışır. Uygulama sorduğunda **Hazır tut: oturum açılınca VortoOne'i başlat** işaretli kalsın. Kapalı ya da uykudaki bir bilgisayar **Çevrimdışı** görünür ve ajanlar onun geri gelmesini bekler.",
        ],
      },
      {
        id: "pausing",
        heading: "Duraklatma ve bağlantıyı kesme",
        blocks: [
          "Bilgisayarda, saatin yanındaki VortoOne simgesi tüm çalışma alanlarının ya da yalnızca birinin ajanlarını duraklatır. **Tercihler\u00a0›\u00a0Genel** içinde her çalışma alanının anahtarı yalnızca onu duraklatır, **⋯** menüsündeki **Ayrıl** ise bağlantısını keser. Duraklatılmışken yeni hiçbir şey başlamaz ve çalışmakta olanlar tamamlanır. Bağlantıyı kesmek ajanların klasörlerini diskte bırakır.",
          "Web sitesinden, cihazın sayfasındaki **Kaldır** onun bağlantısını keser.",
        ],
      },
      {
        id: "organizations",
        heading: "Birden çok çalışma alanı, tek bilgisayar",
        blocks: [
          "Aynı uygulamada her çalışma alanında oturum açın ve her biri için **İzin ver** düğmesine basın. VortoOne Cloud'daki ve kendi sunucularınızdaki çalışma alanları aynı bilgisayarı paylaşabilir (uygulama 0.6.1 ve sonrası). Her birinin orada kendi ajanları ve klasörleri olur ve birinin bağlantısını kesmek diğerlerini etkilemez.",
          "Uygulamanın bu bilgisayarda çalıştırdığı bir sunucu zaten bağlıdır ve hiçbir şey gerektirmez. Bu bilgisayarı başka bir yerdeki bir çalışma alanına da açmak için [Başka bir çalışma alanına da açın](/help/run-on-your-computer#another-workspace) bölümüne bakın.",
          "Uygulamayı bir dizüstünde yalnızca çalışma alanınıza açılan bir pencere olarak mı kullanıyorsunuz? Ajanların dizüstünü kullanması için izin istediğinde **Şimdi değil** derseniz uygulama sade bir pencere olarak kalır.",
          "Terminal satırıyla bağlanan bir sunucu tek bir çalışma alanı için çalışır. Onu başka bir çalışma alanından bağlamak sunucuyu o alana taşır ve ilki artık ona ulaşamaz. Bir bilgisayarda birden çok çalışma alanı için uygulamayı kullanın.",
        ],
      },
      {
        id: "line",
        heading: "Ajanlar sırada beklediğinde",
        blocks: [
          "Bir ajanın her işi bilgisayarın belleğini kullanır. Boş bellek azaldığında sıradaki iş, bir başkası bitene kadar sırada bekler. Önce insanlar gelir. CPU sayısı bunu değiştirmez.",
          "4 GB belleği olan bir bilgisayar aynı anda iki ya da üç iş çalıştırır, 8 GB olan yaklaşık dokuz. Bir Mac ya da PC en fazla sekiz iş çalıştırır. Ajanların açık bıraktığı tarayıcılar ve masaüstleri de bellek kullanır. Daha kısa bir sıra için daha fazla belleği olan bir bilgisayar kullanın ya da bir tane daha bağlayıp ajanları aralarında paylaştırın. Bir bilgisayarı paylaşan çalışma alanları belleğini de paylaşır.",
        ],
      },
      {
        id: "folders",
        heading: "Klasörler",
        blocks: [
          "Her ajanın `~/vortoone/<workspace>` içinde her zaman kendi klasörü vardır. Başka klasörlere izin bilgisayarın kendisinden verilir, asla web sitesinden verilmez: uygulamada **Tercihler › Klasörler** bölümünden, bir sunucudaysa `vortoone-runner folders allow` ile. Her çalışma alanı kendi klasörlerine izin verir: birine verdiğiniz izin yalnızca onun ajanları için geçerlidir. Bir klasörü kaldırmak hiçbir şeyi silmez. Yalnızca erişimi kaldırır.",
        ],
      },
      {
        id: "reach",
        heading: "Ajanlar nelere ulaşabilir",
        blocks: [
          "Ajanlar sizin adınıza, bilgisayarın kullanıcısı olarak çalışır. Dosya araçları kendi klasörlerinde kalır, ama bir komut sizin okuyabildiğiniz her şeyi okuyabilir:",
          {
            list: [
              "dosyalarınız ve oturumlarınız",
              "diğer ajanların klasörleri, başka çalışma alanlarınınkiler dahil, ve tarayıcılarının sakladığı oturumlar",
              "bu bilgisayarı bağlayan jeton",
              "VortoOne'i de çalıştıran bir bilgisayarda, onun `.vortoone` içindeki veritabanı ve anahtarları",
            ],
          },
          "VortoOne anahtarların, token'ların ve oturum bilgilerinin adını geçiren komutları reddeder, ama bu bir kuraldır, duvar değil. **Komutlar** varsayılan olarak açıktır. Onları uygulamanın sorusunda **İzin ver**'e basmadan önce ya da daha sonra **Tercihler\u00a0›\u00a0Araçlar** içinde kapatın. Orada her çalışma alanının kendi anahtarı vardır (uygulama 0.8.1 ve sonrası) ve uygulama birini yeniden açmadan önce sorar. Claude Code ve Codex, Komutlar kapalıyken bile kendi komutlarını ajanın klasöründe, kendi kurallarıyla çalıştırır.",
          "Gerçek bir duvar için bir Linux sunucu kullanın. Debian 13'te `--system` ile kurulduğunda her ajan ayrı bir kullanıcıdır. Ubuntu'da ajanlar tek bir kullanıcıyı, `vortoone` kullanıcısını paylaşır: birbirlerinin klasörlerini görürler, sizinkileri ya da sunucununkileri görmezler. Ya da ajanlara kendilerine ait bir bilgisayar verin.",
        ],
      },
    ],
  },
  "workspace-computer": {
  "title": "Çalışma alanının bilgisayarı",
  "summary": "Güncel VortoOne Desktop sürümünün kurulum paketini ve bağlantı talimatlarını kullanın. Bu sürüm barındırılan bulut hizmeti veya tek komutla sunucu kurulumu sunmaz.",
  "sections": []
},
  import: {
    title: "Ajanlarınızı getirin",
    summary:
      "Ajanları başka bir uygulamadan ya da başka bir VortoOne çalışma alanından talimatları, becerileri, rutinleri ve bellekleriyle birlikte getirin.",
    sections: [
      {
        id: "comes",
        heading: "Neler gelir",
        blocks: [
          {
            list: [
              "Ajanlar, adları, rolleri, talimatları ve görünümleriyle.",
              "Beceriler, onlara sahip olan ajanlar için açık olarak.",
              "Herkese açık kanallar, içlerindeki ajanlarla.",
              "Rutinler, etkin ve kendi zamanlamalarıyla. İsterseniz **Rutinler** bölümünden duraklatın.",
              "Bellek: her ajanın hatırladıkları.",
              "Her ajanın kendi dosyaları (OpenBot.run'ın dosya klasörü, bir OpenClaw çalışma alanı), **Drive** içinde ajanın adını taşıyan bir klasörde. Anahtarlar, oturum bilgileri ve sistem dosyaları geride kalır ve orada zaten olan hiçbir şeyin yerine yenisi konmaz.",
            ],
          },
        ],
      },
      {
        id: "never",
        heading: "Asla gelmeyenler",
        blocks: [
          "Anahtarlar, şifreler, oturum bilgileri ve sohbet geçmişi asla gelmez: ajanlar çalışma alanının yapay zekasıyla düşünür. Bazı şeylerin burada henüz karşılığı yok ya da bir dosyada hiç taşınmamalı. Bunları sonra yeniden oluşturabilirsiniz.",
          "Dosyalar ve klasörler tarayıcınızda okunur. Bir GitHub bağlantısı çalışma alanının sunucusundan geçer. Anahtar ve oturum dosyaları asla açılmaz ve siz onaylayana kadar hiçbir şey değişmez.",
        ],
      },
      {
        id: "mcp",
        heading: "MCP ile bağlan",
        blocks: [
          "MCP, yapay zeka uygulamanızın bu çalışma alanına erişmesini sağlar, böylece ajanları talimatları, becerileri ve rutinleriyle birlikte burada kendisi oluşturur. Uygulamanızı seçin, **MCP ile bağlan** seçeneğini işaretleyin ve sonra:",
          {
            list: [
              "Adımın gösterdiği adresi, uygulamanın kendi adımlarıyla bağlayın. [Uygulama uygulama nasıl bağlanır](/help/apps-de-ia#connect)",
              "Giriş sayfasında **Ajan, rutin ve beceri oluşturma ve düzenleme** seçeneğini seçin.",
              "İsteği uygulamaya yapıştırın. Ajanlar geldikçe adımda görünür.",
            ],
          },
          "Bir uygulama bağlanamıyorsa adımda hangisi görünüyorsa **Dosyadan** ya da **Yalnızca istek** seçeneğini seçin. İstek, [Başka bir uygulamadan](/help/import#other-apps) bölümünde anlatılıyor.",
        ],
      },
      {
        id: "other-apps",
        heading: "Başka bir uygulamadan",
        blocks: [
          "**Grok Bot**, **ChatGPT dots** ya da **Başka bir uygulama** seçeneğini seçin. İsteği kopyalayıp o uygulamaya yapıştırın, sonra döndürdüğünü geri getirin: dosyayı sürükleyip bırakın ya da yanıtın tamamını yapıştırın. Her uygulamada nereye yapıştırılır:",
          {
            list: [
              "**Grok Bot**: botunuzla olan sohbete. JSON ile yanıt verir: yanıtın tamamını kopyalayın.",
              "**ChatGPT dots**: yeni bir sohbete. Oluşturduğu dosyayı indirin.",
              "**Claude**: yeni bir sohbete ya da ajanlarınızın projesine. Oluşturduğu dosyayı indirin.",
              "**Manus**: yeni bir göreve. Dosya, görevin sonuçlarında.",
              "**Rakazo**: ajanınıza gönderin. JSON ile yanıt verir: yanıtın tamamını kopyalayın.",
              "**OpenBot**: istek gerekmez. Tenant klasörünü (agents.yaml, agents/, model.yaml, skills.yaml) sürükleyin ya da GitHub bağlantısını yapıştırın.",
            ],
          },
        ],
      },
      {
        id: "export",
        heading: "Bir çalışma alanını dışa aktarın",
        blocks: [
          "**Ayarlar › İçe ve dışa aktar › Dışa aktar**, ajanlarınızı başka bir çalışma alanına götürmek ya da bir kopyasını saklamak için bir .json dosyası indirir. Dosyada şunlar var:",
          {
            list: [
              "Ajanlar, talimatları, modelleri ve görünümleriyle",
              "Beceriler, SKILL.md dosyasının tamamıyla",
              "Herkese açık kanallar, ajanlarıyla",
              "Zamanlanmış rutinler (diğer tarafa etkin olarak gelir)",
              "Çalışma alanındaki herkesin görebildiği bellek",
            ],
          },
          "Dosyada asla olmayanlar:",
          {
            list: [
              "API anahtarları ve şifreler",
              "Bağlayıcılar ve oturum bilgileri",
              "Bilgisayarlar",
              "Sohbetler, mesajlar ve özel kanallar",
              "Kullanım, maliyetler ve etkinlik geçmişi",
            ],
          },
          "Dosyayı kullanmak için diğer çalışma alanında **Ayarlar › İçe ve dışa aktar** bölümünü açın ve .json dosyasını bırakın.",
        ],
      },
    ],
  },
  wiki: {
    title: "Wiki",
    summary: "Şirketin bildikleri, birbirine bağlı sayfalarda.",
    sections: [
      {
        id: "links",
        heading: "Bağlantılar",
        blocks: [
          "Başka bir sayfaya bağlantı vermek için [[ yazın. Geri bağlantılar ve grafik, sayfaların birbirine nasıl bağlandığını gösterir.",
        ],
      },
      {
        id: "agents",
        heading: "Ajanlar nasıl kullanır",
        blocks: ["Ajanlar yanıt verirken wiki'yi okur. Wiki'ye yalnızca biri istediğinde yazarlar."],
      },
      {
        id: "obsidian",
        heading: "Bir Obsidian kasası getirin",
        blocks: [
          "Kasanın klasörünü ya da onun .zip dosyasını kurulumun Bağlam adımına veya wiki'nin kendi içe aktarma bölümüne bırakın. [[Bağlantılar]], [[not|takma ad]] ve [[not#bölüm]] çalışmaya devam eder. Etiketler frontmatter'dan ve metinden gelir. Ekler Drive'a gider, gömülü görseller ve dosyalar sayfada kartlara dönüşür. Uyarı kutuları uyarı kutusu olarak kalır. Dataview sorguları metin olarak gelir: tabloyu bir ajandan oluşturmasını isteyin. Canvas dosyaları ve Obsidian ayarları (.obsidian) dışarıda kalır.",
        ],
      },
    ],
  },
  memory: {
    title: "Bellek",
    summary: "Her ajanın sohbetlerden ve ekibin düzeltmelerinden öğrendikleri.",
    sections: [
      {
        id: "learns",
        heading: "Ajanlar nasıl öğrenir",
        blocks: [
          "Ajanlar sohbetlerden ve ekibin düzeltmelerinden öğrenir. Bir ajanın bildiği şey, ilgili olduğunda bağlamına girer. **Bellek** sekmesinde yanlış ya da eskimiş olanı düzenleyin, sabitleyin veya arşivleyin.",
        ],
      },
      {
        id: "permissions",
        heading: "Bellek izin vermez",
        blocks: [
          "Bellekteki hiçbir şey bir ajanın daha fazlasını yapmasına izin vermez. Neler yapabileceği **Araçlar ve erişim** bölümünde kalır.",
        ],
      },
      {
        id: "proposals",
        heading: "Önerilen değişiklikler",
        blocks: [
          "Bir ders tekrarlandığında ajan kendi talimatlarını ya da çalışma alanının bağlamını değiştirmeyi önerebilir. Biri kabul edene kadar hiçbir şey değişmez ve metin önce düzenlenebilir. Önceki sürüm geçmişte kalır. Reddedilen bir öneri ders olarak saklanır, böylece tekrar önerilmez.",
        ],
      },
    ],
  },
  approvals: {
    title: "Onaylar",
    summary: "Hassas işlemler bir kişiyi bekler, bir kez ya da kalıcı olarak.",
    sections: [
      {
        id: "what",
        heading: "Neler önce sorar",
        blocks: [
          "Bir ajan merge, deploy ya da geri alma yapmak, dışarıya bir şey göndermek veya veri silmek istediğinde, önce sorar. Bu, her ajanın başladığı **Yalnızca riskli olanlar** düzeyidir. Ajanın sayfasındaki **Onaylar** bölümünde **Her zaman sor** bir şeyi değiştirmeden önce sormasını sağlar, **Hiç sorma** ise merge, deploy, ödemeler ve rutinler dahil hiçbir şey sormamasını sağlar. İstek, tam işlem ve nedeniyle birlikte **Onaylar** bölümünde ve konuşmada görünür.",
        ],
      },
      {
        id: "always",
        heading: "Bir kez ya da her zaman",
        blocks: [
          "Onaylamak, değerleriyle birlikte tek bir işlemi kapsar. Ajan herhangi bir şeyi değiştirirse yeniden sorar.",
          "**Her zaman onayla**, ajanın bu tür bir işlemi bundan sonra her değerle, sormadan yapmasına izin verir. Bir rutinin isteğinde **Bu rutinde her zaman** aynısını yalnızca o rutin için yapar. **Her zaman onayla**, ajan **Yalnızca riskli olanlar** düzeyindeyken merge ve deploy dahil her tür işlem için sunulur. **Bu rutinde her zaman** merge, deploy, geri alma ve benzerleri için sunulmaz. Geri almak için ajanın sayfasındaki **Araçlar ve erişim** bölümüne ya da rutinin ayarlarına gidin.",
        ],
      },
      {
        id: "who",
        heading: "Kim onaylar",
        blocks: [
          {
            list: [
              "Ajanın sahibi, ajanın istekleri için.",
              "Rolünde **Ajanların eylemlerini onaylama** olan herkes, tüm ajanlar için.",
              "Ajanın isterken adını verdiği kişiler, sahibinin yerine.",
              "**Benim yerime kim onaylar** bölümünde birinin yerine bakan kişi, o kişiden istenenler için. Merge, deploy ve diğer riskli işlemler yalnızca bu açıksa.",
            ],
          },
          "Birinin kendi posta kutusundaki bir işlem yalnızca o kişiyi bekler. Bir ajan için yeni erişim, ajanları yönetenleri bekler.",
          "Sağ kolunuz Lodi de diğer ajanların önerdiği yeni ajanları ve bir ajanın modeli, bütçesi ve kanallarındaki değişiklikleri, yalnızca siz istediyseniz, sizin yerinize onaylayabilir. Kimin sizin yerinize baktığını ve Lodi’nin neyi onaylayacağını **Ayarlar\u00a0›\u00a0Siz\u00a0›\u00a0Onaylar** bölümünden seçersiniz.",
        ],
      },
      {
        id: "refusing",
        heading: "Reddetmek",
        blocks: ["İsterseniz nedenini söyleyin: ajan bunu okur, planını ayarlar ve ders olarak saklar."],
      },
      {
        id: "meetings",
        heading: "Toplantılar",
        blocks: ["Ajanlar yalnızca saat önerir. Kişilere davetler her zaman isteyen kişi üzerinden gider."],
      },
    ],
  },
  "who-answers": {
    title: "Bir mesajı kim yanıtlar",
    summary: "Bir bahsetme ajanı seçer. Bahsetme yoksa VortoOne sizin için bu sırayla seçer.",
    sections: [
      {
        id: "order",
        heading: "Bir mesaj ajanını nasıl bulur",
        blocks: [
          "Bir ajandan @ ile bahseden mesaj o ajana gider. Kimseden bahsetmeyen bir mesajı, çalışma alanında olanlar arasında bu listedeki ilki okur:",
          {
            list: [
              "**Jev**, bu tür kararlar için yapılmış küçük ve hızlı bir model.",
              "**Çalışma alanının API anahtarlarından birindeki küçük bir model**, örneğin Claude Haiku 5.5 ya da GPT-6 Luna.",
              "**Kendi Claude ya da ChatGPT planınız**, kendi çalıştırdığınız bir sunucuda yazdığınız mesajlar için. Mesajları giriş yapılmış olduğu bilgisayarda okur ve birkaç saniye sürer.",
              "**Basit kurallar**: bir ajana verilen yanıt o ajana gider, onunla konuştuktan hemen sonra yazdığınız devam mesajı da, tek bir ajanla yalnız olduğunuz bir kanaldaki mesaj da.",
            ],
          },
          "Bunların hiçbiri uymazsa hiçbir ajan yanıt vermez. Bir ajanı çağırmak için ondan bahsedin.",
        ],
      },
      {
        id: "jev",
        heading: "Jev",
        blocks: [
          "Çalışma alanınızı VortoOne barındırıyorsa Jev dahildir. Kendi çalıştırdığınız bir sunucuda sahibin TypeSafe anahtarını kullanır, mesaj başına yaklaşık US$ 0,0001: [Jev'i kurun](/help/jev#setup).",
        ],
      },
      {
        id: "plans",
        heading: "Planınız neden yalnızca sizin mesajlarınız için seçer",
        blocks: [
          "Claude ve ChatGPT planları kişiseldir, bu yüzden planınız yalnızca sizin yazdığınız mesajlar için seçim yapar.",
        ],
      },
      {
        id: "see",
        heading: "Kimin seçtiğini görmek",
        blocks: ["Bir mesajın altındaki onay işaretini açın. Son satırı, ajanları kimin seçtiğini söyler."],
      },
    ],
  },
  jev: {
    title: "Jev",
    summary:
      "TypeSafe'in, ajanlarınız için hızlı kararlar veren hızlı modeli. Kendi sunucunuzda isteğe bağlıdır ve kendi anahtarınızla çalışır.",
    sections: [
      {
        id: "what",
        heading: "Ne yapar",
        blocks: [
          {
            list: [
              "**Kimin yanıtlayacağını seçer.** Kimseden @ ile bahsetmeyen bir mesaj, ajanlarınız bir planla düşünse ve hiç API anahtarınız olmasa bile bir saniyeden kısa sürede doğru ajana gider.",
              "**Belleği düzenli tutar.** Bir tekrarı ya da daha yeni bir bilgiyi başka sözcüklerle söylense bile fark eder: tekrar, ajanın bildiğiyle birleşir, daha yeni bilgi eskisinin yerini alır. Günde bir kez geri kalanı toplar.",
              "**Ekranı görür.** Ajanlar tarayıcılarında ve masaüstlerinde düğmeleri ve alanları bir tariften bulur, küçük olanlara daha yakından bakar ve bir adımın işe yaradığını ekran görüntüsünden kontrol eder.",
              "**Daha az okutur.** Her turda yalnızca ajanın büyük olasılıkla ihtiyaç duyacağı araçları yükler, böylece yapay zekanız daha az okur ve daha az tutar.",
            ],
          },
        ],
      },
      {
        id: "without",
        heading: "Jev olmadan",
        blocks: [
          "Her şey çalışmaya devam eder, daha yavaş ve daha az özenle.",
          {
            list: [
              "**Kim yanıtlar:** API anahtarlarınızdan birindeki küçük bir model seçer, örneğin Claude Haiku 5.5 ya da GPT-6 Luna. Anahtar yoksa her kişinin kendi Claude ya da ChatGPT planı, o kişinin yazdığı mesajlar için birkaç saniyede seçer. Bunlar da yoksa bir yanıt ya da devam mesajı yine ajanına ulaşır. Bir ajanı çağırmak için ondan bahsedin. [Ajanlar nasıl seçilir](/help/who-answers)",
              "**Bellek:** aynı sözcüklerle yapılan tekrarlar yine birleşir. Başka sözcüklerle yapılan tekrarlar ve güncellemeler ayrı kalır.",
              "**Ekran:** ajanlar ekranda gösterilen numaraları ve tam adları kullanır. Görsellerden hiçbir şey okunmaz.",
            ],
          },
        ],
      },
      {
        id: "setup",
        heading: "Kurulum",
        blocks: [
          {
            list: [
              "https://console.typesafe.ai adresini açın ve Google ya da e-postanızla giriş yapın. Yeni hesap da aynı şekilde açılır.",
              "Konsolda kredi ekleyin. Jev ön ödemelidir.",
              "https://console.typesafe.ai/keys adresini açın, bir anahtar oluşturun ve hemen kopyalayın.",
              "VortoOne'te **Ayarlar › Yapay zeka ve kullanım** bölümünü açın ve anahtarı **Jev** altına yapıştırın. Onu yalnızca bu sunucuyu kuran kişi görür.",
            ],
          },
          "Docker ile çalıştırdığınız bir sunucuda anahtarı bunun yerine `.env` dosyasına `TYPESAFE_API_KEY` olarak koyabilirsiniz. Bu anahtar, Ayarlar'dakinin önüne geçer.",
        ],
      },
      {
        id: "cost",
        heading: "Ne kadar tutar",
        blocks: [
          {
            list: [
              "Jev'in okuduğu her milyon token için US$ 0,042. Yanıtları ücretsizdir. Kimin yanıtlayacağını seçmek yaklaşık 2.500 token okur, yani yaklaşık US$ 0,0001. Bir ekran görüntüsü yaklaşık 3.900 token eder.",
              "Satın aldığınız krediler 12 ay geçerlidir. Jev'in kredisi bitmesin diye konsolda otomatik yüklemeyi açın.",
              "**Ayarlar › Yapay zeka ve kullanım**, Jev'in bu ay kaç karar verdiğini ve bunların ne kadar tuttuğunu gösterir.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "Verileriniz",
        blocks: [
          "Jev'in karar vermek için okudukları:",
          {
            list: [
              "Kanalın adı ve açıklaması, mesaj ve ondan önceki birkaç mesaj",
              "Ajanlarınızın adları ve rolleri, talimatlarının başı ve kullanabildikleri araçlar",
              "Karşılaştırdığı bellek öğeleri",
              "Bir ajanın görevi ve araç sonuçlarının başı",
              "Bir misyonun hedefi ve bir ajanın bunun için çalıştırmak istediği komutlar",
              "Bağlı bir uygulamanın sunduğu araçlar",
              "Bir ajanın kullandığı sayfa ya da ekran, ekran görüntüleri dahil",
            ],
          },
          "TypeSafe, Jev'i Amerika Birleşik Devletleri'nde çalıştırır ve modellerini gönderdiklerinizle eğitmez. Verileri hizmeti sürdürmek için gerektiği kadar saklar. Kurumsal planlarında sıfır saklama seçeneği vardır.",
          "TypeSafe'in gizlilik politikası: https://typesafe.ai/legal/privacy-policy. Veri işleme sözleşmesi: https://typesafe.ai/legal/data-processing.",
        ],
      },
      {
        id: "problems",
        heading: "Durduğunda",
        blocks: [
          {
            list: [
              "**Anahtar kabul edilmedi:** yeni bir anahtar oluşturun ve **Ayarlar › Yapay zeka ve kullanım** bölümüne yapıştırın.",
              "**Kredi bitti:** konsolda kredi ekleyin. Jev 30 dakika içinde yeniden dener ya da **Tekrar kontrol et** düğmesine tıklayın.",
              "**Meşgul ya da ulaşılamıyor:** TypeSafe saniye başına istekleri sınırlayabilir ya da bir süre yanıt vermeyebilir. Jev kendiliğinden yeniden devreye girer. Limitleriniz: https://console.typesafe.ai/rate-limits. Daha yüksek limitler için sales@typesafe.ai adresine yazın.",
            ],
          },
          "Bu sırada ajanlar [Jev olmadan](/help/jev#without) bölümündeki gibi çalışır.",
        ],
      },
    ],
  },
  "apps-de-ia": {
    title: "Yapay zeka uygulamaları",
    summary:
      "Claude, Cursor ya da başka bir MCP uygulamasından çıkmadan VortoOne ajanlarına görev verin ve yanıtı orada alın.",
    sections: [
      {
        id: "what",
        heading: "Nasıl çalışır",
        blocks: [
          "VortoOne bir MCP sunucusudur. Adresini yapay zeka uygulamanıza yapıştırır, hesabınızla giriş yapar ve bir çalışma alanı seçersiniz. Bundan sonra uygulama ajanları ve kanalları listeleyebilir, sizin adınıza bir ajana görev gönderebilir, yanıtı bekleyebilir ve son görevleri okuyabilir.",
          "Örneğin Unity ya da Blender'da bir sahnenin ortasında Claude'a şunu söyleyin: \"Pazarlama ajanından bir lansman metni iste\". Mesaj VortoOne'te sizin mesajınız olarak, **Claude üzerinden** işaretiyle görünür ve ajanın yanıtı Claude'a geri gelir.",
          "Adres **Ayarlar › Yapay zeka uygulamaları** bölümündedir. Sonu `/mcp` ile biter.",
        ],
      },
      {
        id: "connect",
        heading: "Bir uygulama bağlayın",
        blocks: [
          {
            list: [
              "**Claude** (claude.ai, Claude Desktop ve telefon): **Customize › Connectors** içinde **+ Add**'e, ardından **Add custom connector**'a tıklayın, adını VortoOne koyun, adresi yapıştırın ve **Continue**'ya tıklayın. **Sign in now** seçeneğini seçin, **Add**'e tıklayın ve yetki verin. Team ve Enterprise planlarında bağlayıcıyı Claude organizasyonunun Owner'ı Organization settings › Connectors içinden ekler ve her kişi Customize › Connectors içinde **Connect**'e tıklar. Claude'un ücretsiz planında bir özel bağlayıcıya yer vardır.",
              "**Claude Code:** `claude mcp add --transport http --scope user VortoOne <address>` komutunu çalıştırın, ardından `/mcp` yazın, VortoOne'i seçin ve **Authenticate**'e tıklayın. Yalnızca geçerli klasör için `--scope user` kısmını çıkarın.",
              "**Cursor:** **Ayarlar › Yapay zeka uygulamaları** içindeki **Cursor'a ekle** ya da adresi `~/.cursor/mcp.json` dosyasına ekleyin. Cursor ilk kullanımda giriş yapmanızı ister.",
              '**VS Code:** **VS Code\'a ekle** ya da adresi `"type": "http"` ile `.vscode/mcp.json` dosyasına ekleyin.',
              "**Diğer uygulamalar:** ChatGPT, Gemini, Grok, Perplexity, Codex, Zed, JetBrains ve daha fazlasının adımları, adresiniz önceden doldurulmuş olarak **Uygulama bağla** içinde.",
            ],
          },
          "Giriş yaptığınızda VortoOne, erişimi hangi uygulamanın istediğini ve nereye döneceğini gösterir. Yalnızca bağlantıyı az önce siz başlattıysanız devam edin.",
        ],
      },
      {
        id: "tools",
        heading: "Uygulama neler yapabilir",
        blocks: [
          {
            list: [
              "`send_to_agent`: sizin adınıza bir ajana, onunla özel sohbetinizde ya da bir kanalda mesaj gönderir ve yanıtı bekler. Yalnızca **Okuma ve ajanlara görev gönderme** erişimiyle.",
              "`wait_for_reply`: bir görevin yanıtını beklemeye devam eder.",
              "`list_agents`: gördüğünüz ajanlar.",
              "`list_channels`: okuduğunuz kanallar.",
              "`read_conversation`: bir kanalın, bir ileti dizisinin ya da bir görevin son mesajları.",
              "`list_recent_tasks`: uygulamalardan gönderdiğiniz görevler ve ajanların şu anda yaptıkları.",
            ],
          },
          "Uygulama sizin adınıza hareket eder ve yalnızca sizin gördüklerinizi görür. Ajanların işlemlerini asla onaylamaz: bir ajan onay istemek için durduğunda yanıt bunu bir bağlantıyla söyler ve kararı VortoOne'te bir kişi verir. Ayarları, üyeleri ya da izinleri de değiştirmez.",
        ],
      },
      {
        id: "workspace",
        heading: "Her bağlantıya bir çalışma alanı",
        blocks: [
          "Her bağlantı, giriş yaparken seçilen çalışma alanını kapsar. Başka bir çalışma alanını kullanmak için uygulamayı yeniden bağlayın ve diğerini seçin.",
          "**Ayarlar › Yapay zeka uygulamaları** bölümünde bağlı uygulamaları, her birinin ne zaman kullanıldığını görür ve istediğinizin bağlantısını kesebilirsiniz. Üyeleri yöneten kişiler tüm çalışma alanının uygulamalarını görür ve onların da bağlantısını kesebilir. Bir uygulamadan gönderilen her mesaj Denetim'de görünür.",
        ],
      },
      {
        id: "tokens",
        heading: "Kişisel token'lar",
        blocks: [
          "OAuth ile giriş yapamayan betikler ve uygulamalar için **Ayarlar › Yapay zeka uygulamaları** bölümünde kişisel bir token oluşturun ve `Authorization: Bearer` başlığında gönderin. Token bir kez gösterilir, yalnızca oluşturulduğu çalışma alanında çalışır ve 30 gün, 90 gün, 1 yıl sonra ya da hiç sona ermeyecek şekilde ayarlanabilir.",
          "https olmayan bir sunucuda, sunucunun kendi bilgisayarından uzaktayken kişisel bir token kullanın: OAuth ile giriş https gerektirir.",
        ],
      },
      {
        id: "limits",
        heading: "Limitler",
        blocks: [
          {
            list: [
              "Bağlantı başına dakikada en fazla 120, günde en fazla 5.000 istek.",
              "Her çalışma alanında kişi başına dakikada en fazla 20, günde en fazla 500 mesaj.",
              "Bir bekleme en fazla 45 saniye sürer. Ajan hâlâ çalışıyorsa uygulama yeniden bekler.",
            ],
          },
        ],
      },
      {
        id: "safety",
        heading: "Başkalarının metinleri",
        blocks: [
          "Yanıtlar, VortoOne'teki ajanların ve kişilerin yazdığı metinleri taşır. Uygulama bu metinleri talimat olarak değil içerik olarak işaretlenmiş şekilde alır, ama bir metin yine de uygulamayı başka bir şeye ikna etmeye çalışabilir. Onaylamadan önce uygulamanızın ne yapmak üzere olduğunu kontrol edin.",
        ],
      },
      {
        id: "problems",
        heading: "Bağlanmıyorsa",
        blocks: [
          {
            list: [
              "**İsteğin süresi doldu:** giriş sayfası 30 dakika geçerlidir ve bir kez çalışır. Uygulamaya dönün ve yeniden bağlanın.",
              "**Uygulama yeniden giriş yapmanızı istiyor:** bağlantı kesildi, şifre değişti ya da çalışma alanından ayrıldınız. Uygulamadan yeniden giriş yapın.",
              "**Cursor eski bir girişte takılı kaldı:** Cursor'dan çıkın, `~/.mcp-auth` klasörünü silin ve yeniden bağlanın.",
            ],
          },
        ],
      },
    ],
  },
  "install-on-a-server": {
  "title": "VortoOne'i bir sunucuya kurun",
  "summary": "Güncel VortoOne Desktop sürümünün kurulum paketini ve bağlantı talimatlarını kullanın. Bu sürüm barındırılan bulut hizmeti veya tek komutla sunucu kurulumu sunmaz.",
  "sections": []
},
};
