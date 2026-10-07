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
    .map(x => x.summary || x.title);

  const stories = [];
  const perSection = new Map();

  for (const row of remaining) {
    const count = perSection.get(row.beat) || 0;
    if (count >= 2) continue;
    stories.push({
      section: row.section,
      title: row.title,
      deck: row.summary || "",
      source: row.source || "",
      age: relativeAge(row.latest_seen || row.first_seen),
      type: titleCaseEditorialType(row.editorial_type),
      url: row.url || "",
      image: row.image_url || ""
    });
    perSection.set(row.beat, count + 1);
    if (stories.length >= 18) break;
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
      url: lead.url || "",
      image: lead.image_url || ""
    },
    briefing: brief,
    stories
  };
}

async function fetchLiveEditions() {
  const cfg = window.NEWSREADER_CONFIG || {};
  if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) return {};

  const url = new URL("/rest/v1/editions", cfg.supabaseUrl);
  url.searchParams.set("select", "edition_type,edition_date,generated_at,payload");
  url.searchParams.set("edition_type", "in.(morning,evening)");
  url.searchParams.set("order", "edition_date.desc,generated_at.desc");
  url.searchParams.set("limit", "4");

  const response = await fetch(url.toString(), {
    headers: {
      apikey: cfg.supabaseAnonKey,
      Authorization: `Bearer ${cfg.supabaseAnonKey}`
    },
    cache: "no-store"
  });
  if (!response.ok) throw new Error(`Supabase returned ${response.status}`);
  const rows = await response.json();
  const out = {};
  for (const row of rows) {
    if (!out[row.edition_type]) out[row.edition_type] = toViewModel(row);
  }
  return out;
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


function savedKey(story) {
  return story.url || `${story.section}|${story.title}`;
}

function getSaved() {
  try { return JSON.parse(localStorage.getItem("morningPaperSaved") || "[]"); }
  catch { return []; }
}

function setSaved(items) {
  localStorage.setItem("morningPaperSaved", JSON.stringify(items));
}

function isSaved(story) {
  const key = savedKey(story);
  return getSaved().some(x => x.key === key);
}

function toggleSaved(story) {
  const key = savedKey(story);
  let items = getSaved();
  const exists = items.some(x => x.key === key);
  if (exists) items = items.filter(x => x.key !== key);
  else items.unshift({key, ...story});
  setSaved(items);
  return !exists;
}

function renderSavedView() {
  const items = getSaved();
  document.getElementById("lead").innerHTML = "";
  document.getElementById("briefing").innerHTML = `
    <div class="kicker">SAVED</div>
    <h2>Your Saved Stories</h2>
    <p>${items.length ? "Stories you bookmarked on this device." : "No saved stories yet."}</p>
  `;
  const grid = document.getElementById("sectionGrid");
  grid.innerHTML = "";
  const tpl = document.getElementById("storyCardTemplate");
  items.forEach(story => {
    const node = tpl.content.cloneNode(true);
    const card=node.querySelector(".story-card");
    card.dataset.section=story.section || "SAVED";
    node.querySelector(".story-kicker").textContent=story.section || "SAVED";
    const h3=node.querySelector("h3");
    if(story.url){
      const a=document.createElement("a");
      a.href=story.url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=story.title;
      h3.appendChild(a);
    }else h3.textContent=story.title;
    const deck=node.querySelector(".story-deck");
    deck.textContent=story.deck || "";
    if(!story.deck) deck.style.display="none";
    node.querySelector(".story-meta").innerHTML=`<span class="story-source">${story.source || ""}</span><button class="inline-save saved">Saved</button>`;
    grid.appendChild(node);
  });
}

function renderSectionsView() {
  document.getElementById("lead").innerHTML = "";
  document.getElementById("briefing").innerHTML = `
    <div class="kicker">SECTIONS</div>
    <h2>Browse the Paper</h2>
    <p>Jump directly to the beats in your personal edition.</p>
  `;
  const grid=document.getElementById("sectionGrid");
  grid.innerHTML="";
  const names=[...new Set(currentEdition.stories.map(x=>x.section))];
  names.forEach(name=>{
    const article=document.createElement("article");
    article.className="section-tile";
    article.innerHTML=`<div class="kicker">${name}</div><h3>${currentEdition.stories.filter(x=>x.section===name).length} stories</h3>`;
    article.onclick=()=>{
      renderEdition(currentEdition,currentIsLive);
      setTimeout(()=>{
        [...document.querySelectorAll(".story-card")].find(c=>c.dataset.section===name)?.scrollIntoView({behavior:"smooth",block:"start"});
      },0);
    };
    grid.appendChild(article);
  });
}


let currentEdition = demoEdition;
let currentIsLive = false;
let liveEditions = {};
let currentEditionType = "morning";

function renderEdition(edition, live) {
  currentEdition = edition;
  currentIsLive = live;
  document.getElementById("issueDate").textContent = edition.date;
  const pill = document.querySelector(".edition-pill");
  if (pill) pill.textContent = currentEditionType === "evening" ? "EVENING EDITION" : "MORNING EDITION";
  const masthead = document.querySelector(".masthead h1");
  if (masthead) masthead.textContent = currentEditionType === "evening" ? "Evening Paper" : "Morning Paper";
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
        <button id="leadSave" class="inline-save">${isSaved(edition.lead) ? "Saved" : "Save"}</button>
      </div>
    </div>
    ${edition.lead.image ? `<figure class="lead-image"><img src="${edition.lead.image}" alt="" loading="eager" referrerpolicy="no-referrer"><figcaption>${edition.lead.source}</figcaption></figure>` : (live ? "" : `<div class="lead-art" role="img" aria-label="Editorial illustration"><div class="art-caption">Morning Edition · ${edition.lead.section}</div></div>`)}
  `;

  document.getElementById("briefing").innerHTML = `
    <div class="kicker">BRIEFING</div>
    <h2>Your ${currentEditionType === "evening" ? "Evening" : "Morning"} Brief</h2>
    <p>${currentEditionType === "evening"
      ? "What materially changed since the morning paper — plus what is worth reading tonight."
      : "The three stories most worth knowing before you get into the rest of the paper."}</p>
    <div class="briefing-list">${edition.briefing.map(x => `<div>${x}</div>`).join("")}</div>
  `;

  const grid = document.getElementById("sectionGrid");
  const tpl = document.getElementById("storyCardTemplate");
  grid.innerHTML = "";

  document.getElementById("leadSave")?.addEventListener("click",(e)=>{
    e.preventDefault();
    const btn=e.currentTarget;
    const nowSaved=toggleSaved(edition.lead);
    btn.textContent=nowSaved ? "Saved" : "Save";
    btn.classList.toggle("saved",nowSaved);
  });

  edition.stories.forEach(story => {
    const node = tpl.content.cloneNode(true);
    const card = node.querySelector(".story-card");
    card.dataset.section = story.section;
    card.dataset.link = story.url ? "true" : "false";
    node.querySelector(".story-kicker").textContent = story.section;
    if (story.image) {
      const img = document.createElement("img");
      img.className = "story-image";
      img.src = story.image;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      card.insertBefore(img, node.querySelector("h3"));
    }
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
      `<span class="story-source">${story.source}</span>${story.age ? ` · ${story.age}` : ""}<span class="editorial-badge">${story.type || "News"}</span><button class="inline-save">${isSaved(story) ? "Saved" : "Save"}</button>`;
    const saveBtn=node.querySelector(".inline-save");
    saveBtn?.addEventListener("click",(e)=>{
      e.preventDefault();e.stopPropagation();
      const nowSaved=toggleSaved(story);
      saveBtn.textContent=nowSaved ? "Saved" : "Save";
      saveBtn.classList.toggle("saved",nowSaved);
    });
    grid.appendChild(node);
  });
}


function switchEdition() {
  const target = currentEditionType === "morning" ? "evening" : "morning";
  if (!liveEditions[target]) return;
  currentEditionType = target;
  renderEdition(liveEditions[target], true);
  window.scrollTo({top:0, behavior:"smooth"});
}


let refreshTimer = null;
let lastLiveSignature = "";

function editionSignature(editions) {
  const parts = [];
  for (const key of ["morning","evening"]) {
    const ed = editions[key];
    if (!ed) continue;
    parts.push(key, ed.date || "", ed.lead?.title || "", String(ed.stories?.length || 0));
  }
  return parts.join("|");
}

async function refreshLiveEditions({forceRender=false} = {}) {
  try {
    const updated = await fetchLiveEditions();
    const sig = editionSignature(updated);
    const changed = sig && sig !== lastLiveSignature;

    liveEditions = updated;
    lastLiveSignature = sig;

    const hour = new Date().getHours();
    let preferred = (hour >= 17 && liveEditions.evening) ? "evening" : "morning";
    if (!liveEditions[preferred]) preferred = liveEditions.morning ? "morning" : "evening";

    if (forceRender || changed) {
      currentEditionType = preferred;
      const live = liveEditions[currentEditionType];
      if (live) renderEdition(live, true);
    }

    const pill=document.querySelector(".edition-pill");
    if (pill && liveEditions.morning && liveEditions.evening) {
      pill.classList.add("switchable");
      pill.title="Tap to switch editions";
      if (!pill.dataset.bound) {
        pill.addEventListener("click", switchEdition);
        pill.dataset.bound="1";
      }
    }
  } catch (err) {
    console.error("Live edition refresh failed.", err);
  }
}

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => refreshLiveEditions(), 15 * 60 * 1000);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshLiveEditions();
  });

  window.addEventListener("focus", () => refreshLiveEditions());
}

async function boot() {
  renderNav();
  renderEdition(demoEdition, false);
  await refreshLiveEditions({forceRender:true});
  startAutoRefresh();
}

document.querySelectorAll(".bottom-nav button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".bottom-nav button").forEach(x => x.classList.remove("active"));
    btn.classList.add("active");
    if (btn.dataset.view === "home") { renderEdition(currentEdition,currentIsLive); window.scrollTo({top:0, behavior:"smooth"}); }
    if (btn.dataset.view === "sections") renderSectionsView();
    if (btn.dataset.view === "saved") renderSavedView();
  });
});

boot();
