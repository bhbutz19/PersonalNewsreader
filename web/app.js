const demoEdition = {
  date: "MONDAY, OCT 6, 2026",
  lead: {
    section: "WASHINGTON",
    title: "Grubb’s Pharmacy Adds a Tea Lab — and Keeps Its Vaccine Counter Busy",
    deck: "A neighborhood institution keeps evolving while staying rooted in the practical things Washingtonians rely on.",
    source: "PoPville",
    age: "1h ago",
    type: "News",
    url: ""
  },
  briefing: [
    "D.C. local stories dominate the morning edition",
    "National politics is quieter after filtering duplicate and utility links",
    "Spain now draws from the dedicated El País España feed"
  ],
  stories: [
    {section:"NATIONAL",title:"Trump Announces a Plan to Send $90 Checks to Millions of Medicare Recipients",deck:"A policy proposal leads the national politics section after primary-document noise is filtered out.",source:"Washington Sun",age:"2h ago",type:"News",url:""},
    {section:"D.C. DINING",title:"District 7 Bar & Grill",deck:"A new local dining item makes the cut as national Eater noise falls away.",source:"Eater DC",age:"4h ago",type:"News",url:""},
    {section:"MILWAUKEE",title:"MKE Airport Celebrates 100 Years of Flight!",deck:"Milwaukee marks a century of aviation with a local milestone story.",source:"Urban Milwaukee",age:"3h ago",type:"News",url:""},
    {section:"WISCONSIN",title:"Wisconsin Morning Brief",deck:"Statewide reporting is now filtered to remove newsletter utility fragments.",source:"WPR",age:"3h ago",type:"News",url:""},
    {section:"PACKERS",title:"Inbox: Hopefully, the Packers did both Sunday",deck:"Green Bay analysis leads the Packers section, with repeat stories clustered together.",source:"Packers.com",age:"5h ago",type:"Analysis",url:""},
    {section:"BREWERS",title:"Brewers–Padres NLDS Game 3 FAQ",deck:"The latest postseason matchup information from the Brewers’ official feed.",source:"MLB.com",age:"4h ago",type:"News",url:""},
    {section:"FORMULA 1",title:"F1 fashion goes local as Ferrari, McLaren and Mercedes celebrate Singapore",deck:"The paddock shifts attention to Singapore as teams lean into the local scene.",source:"Motorsport.com",age:"3h ago",type:"Analysis",url:""},
    {section:"SPAIN",title:"El Rey pide que la UE afronte unida las amenazas “vengan de donde vengan”",deck:"The Spain section now uses the dedicated El País España feed.",source:"El País",age:"2h ago",type:"News",url:""}
  ]
};

const SECTION_LABELS = {
  dc_local: "WASHINGTON",
  dc_politics: "D.C. POLITICS",
  us_politics: "NATIONAL",
  dc_dining: "D.C. DINING",
  milwaukee: "MILWAUKEE",
  wisconsin: "WISCONSIN",
  wisconsin_politics: "WISCONSIN POLITICS",
  cedarburg: "CEDARBURG",
  packers: "PACKERS",
  brewers: "BREWERS",
  uwm: "UW–MILWAUKEE",
  f1: "FORMULA 1",
  spain: "SPAIN",
  menorca: "MENORCA",
  logrono: "LOGROÑO / LA RIOJA",
  real_estate: "PROPERTY",
  cooking: "COOKING",
  sailing: "SAILING"
};

const NAV_SECTIONS = [
  ["Home", null],
  ["Washington", "WASHINGTON"],
  ["National", "NATIONAL"],
  ["Dining", "D.C. DINING"],
  ["Milwaukee", "MILWAUKEE"],
  ["Packers", "PACKERS"],
  ["Brewers", "BREWERS"],
  ["F1", "FORMULA 1"],
  ["Spain", "SPAIN"]
];

function titleCaseEditorialType(value) {
  if (!value) return "News";
  if (value === "primary_source") return "Primary Source";
  if (value === "reported_news") return "News";
  if (value === "official_team") return "News";
  if (value === "social") return "Conversation";
  return value.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
}

function relativeAge(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${Math.max(1, mins)}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

function formatIssueDate(value) {
  const d = value ? new Date(value + "T12:00:00") : new Date();
  return d.toLocaleDateString("en-US", {
    weekday: "long", month: "short", day: "numeric", year: "numeric"
  }).toUpperCase();
}

function flattenPayload(payload) {
  const rows = [];
  for (const section of payload.sections || []) {
    for (const story of section.stories || []) {
      rows.push({
        ...story,
        beat: section.beat,
        section: SECTION_LABELS[section.beat] || (section.name || section.beat || "NEWS").toUpperCase()
      });
    }
  }
  return rows;
}

function toViewModel(record) {
  const payload = record?.payload || {};
  const rows = flattenPayload(payload);
  if (!rows.length) return null;

  // Prefer Washington/local for the lead; otherwise take the highest-ranked story.
  const lead = rows.find(x => x.beat === "dc_local")
    || [...rows].sort((a,b) => (b.score || 0) - (a.score || 0))[0];

  const remaining = rows.filter(x => x !== lead);

  const brief = [...remaining]
    .sort((a,b) => (b.score || 0) - (a.score || 0))
    .slice(0, 3)
    .map(x => x.title);

  const stories = [];
  const perSection = new Map();

  for (const row of remaining) {
    const count = perSection.get(row.beat) || 0;
    if (count >= 3) continue;
    stories.push({
      section: row.section,
      title: row.title,
      deck: row.summary || "",
      source: row.source || "",
      age: relativeAge(row.latest_seen || row.first_seen),
      type: titleCaseEditorialType(row.editorial_type),
      url: row.url || ""
    });
    perSection.set(row.beat, count + 1);
    if (stories.length >= 24) break;
  }

  return {
    date: formatIssueDate(record.edition_date || payload.edition_date),
    lead: {
      section: lead.section,
      title: lead.title,
      deck: lead.summary || "",
      source: lead.source || "",
      age: relativeAge(lead.latest_seen || lead.first_seen),
      type: titleCaseEditorialType(lead.editorial_type),
      url: lead.url || ""
    },
    briefing: brief,
    stories
  };
}

async function fetchLiveEdition() {
  const cfg = window.NEWSREADER_CONFIG || {};
  if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) return null;

  const url = new URL("/rest/v1/editions", cfg.supabaseUrl);
  url.searchParams.set("select", "edition_type,edition_date,generated_at,payload");
  url.searchParams.set("edition_type", "eq.morning");
  url.searchParams.set("order", "edition_date.desc,generated_at.desc");
  url.searchParams.set("limit", "1");

  const response = await fetch(url.toString(), {
    headers: {
      apikey: cfg.supabaseAnonKey,
      Authorization: `Bearer ${cfg.supabaseAnonKey}`
    },
    cache: "no-store"
  });
  if (!response.ok) throw new Error(`Supabase returned ${response.status}`);
  const rows = await response.json();
  return rows[0] ? toViewModel(rows[0]) : null;
}

function renderNav() {
  const sectionNav = document.getElementById("sectionNav");
  sectionNav.innerHTML = "";
  NAV_SECTIONS.forEach(([name, sectionName], i) => {
    const b = document.createElement("button");
    b.textContent = name;
    if (i === 0) b.classList.add("active");
    b.onclick = () => {
      sectionNav.querySelectorAll("button").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      if (!sectionName) {
        window.scrollTo({top:0, behavior:"smooth"});
        return;
      }
      const el = [...document.querySelectorAll(".story-card")]
        .find(card => card.dataset.section === sectionName);
      el?.scrollIntoView({behavior:"smooth", block:"start"});
    };
    sectionNav.appendChild(b);
  });
}

function storyLinkStart(url) {
  return url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">` : "";
}
function storyLinkEnd(url) {
  return url ? "</a>" : "";
}

function renderEdition(edition, live) {
  document.getElementById("issueDate").textContent = edition.date;
  const status = document.getElementById("liveStatus");
  status.textContent = live ? "LIVE EDITION" : "DEMO EDITION";
  status.dataset.state = live ? "live" : "demo";

  document.getElementById("lead").innerHTML = `
    <div class="lead-copy">
      <div class="kicker">${edition.lead.section}</div>
      ${storyLinkStart(edition.lead.url)}
      <h2>${edition.lead.title}</h2>
      ${storyLinkEnd(edition.lead.url)}
      ${edition.lead.deck ? `<p class="deck">${edition.lead.deck}</p>` : ""}
      <div class="meta">
        <span>${edition.lead.source}</span>
        ${edition.lead.age ? `<span>•</span><span>${edition.lead.age}</span>` : ""}
        <span class="editorial-badge">${edition.lead.type || "News"}</span>
      </div>
    </div>
    <div class="lead-art" role="img" aria-label="Editorial illustration">
      <div class="art-caption">${live ? "Live Morning Edition" : "Morning Edition"} · ${edition.lead.section}</div>
    </div>
  `;

  document.getElementById("briefing").innerHTML = `
    <div class="kicker">BRIEFING</div>
    <h2>Your Morning Brief</h2>
    <p>The three stories most worth knowing before you get into the rest of the paper.</p>
    <div class="briefing-list">${edition.briefing.map(x => `<div>${x}</div>`).join("")}</div>
  `;

  const grid = document.getElementById("sectionGrid");
  const tpl = document.getElementById("storyCardTemplate");
  grid.innerHTML = "";

  edition.stories.forEach(story => {
    const node = tpl.content.cloneNode(true);
    const card = node.querySelector(".story-card");
    card.dataset.section = story.section;
    card.dataset.link = story.url ? "true" : "false";
    node.querySelector(".story-kicker").textContent = story.section;
    const h3 = node.querySelector("h3");
    if (story.url) {
      const a = document.createElement("a");
      a.href = story.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = story.title;
      h3.appendChild(a);
    } else {
      h3.textContent = story.title;
    }
    const deck = node.querySelector(".story-deck");
    deck.textContent = story.deck || "";
    if (!story.deck) deck.style.display = "none";
    node.querySelector(".story-meta").innerHTML =
      `<span class="story-source">${story.source}</span>${story.age ? ` · ${story.age}` : ""}<span class="editorial-badge">${story.type || "News"}</span>`;
    grid.appendChild(node);
  });
}

async function boot() {
  renderNav();
  renderEdition(demoEdition, false);
  try {
    const live = await fetchLiveEdition();
    if (live) renderEdition(live, true);
  } catch (err) {
    console.error("Live edition unavailable; using demo.", err);
  }
}

document.querySelectorAll(".bottom-nav button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".bottom-nav button").forEach(x => x.classList.remove("active"));
    btn.classList.add("active");
    if (btn.dataset.view === "home") window.scrollTo({top:0, behavior:"smooth"});
  });
});

boot();
