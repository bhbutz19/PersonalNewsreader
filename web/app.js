const edition = {
  date: "MONDAY, OCT 6, 2026",
  lead: {
    section: "WASHINGTON",
    title: "Grubb’s Pharmacy Adds a Tea Lab — and Keeps Its Vaccine Counter Busy",
    deck: "A neighborhood institution keeps evolving while staying rooted in the practical things Washingtonians rely on.",
    source: "PoPville",
    age: "1h ago"
  },
  briefing: [
    "D.C. local stories dominate the morning edition",
    "National politics is quieter after filtering duplicate and utility links",
    "Spain now draws from the dedicated El País España feed"
  ],
  stories: [
    {section:"NATIONAL",title:"Trump Announces a Plan to Send $90 Checks to Millions of Medicare Recipients",deck:"A policy proposal leads the national politics section after primary-document noise is filtered out.",source:"Washington Sun",age:"2h ago"},
    {section:"D.C. DINING",title:"District 7 Bar & Grill",deck:"A new local dining item makes the cut as national Eater noise falls away.",source:"Eater DC",age:"4h ago"},
    {section:"MILWAUKEE",title:"MKE Airport Celebrates 100 Years of Flight!",deck:"Milwaukee marks a century of aviation with a local milestone story.",source:"Urban Milwaukee",age:"3h ago"},
    {section:"WISCONSIN",title:"Wisconsin Morning Brief",deck:"Statewide reporting is now filtered to remove newsletter utility fragments.",source:"WPR",age:"3h ago"},
    {section:"PACKERS",title:"Inbox: Hopefully, the Packers did both Sunday",deck:"Green Bay analysis leads the Packers section, with repeat stories clustered together.",source:"Packers.com",age:"5h ago"},
    {section:"BREWERS",title:"Brewers–Padres NLDS Game 3 FAQ",deck:"The latest postseason matchup information from the Brewers’ official feed.",source:"MLB.com",age:"4h ago"},
    {section:"FORMULA 1",title:"F1 fashion goes local as Ferrari, McLaren and Mercedes celebrate Singapore",deck:"The paddock shifts attention to Singapore as teams lean into the local scene.",source:"Motorsport.com",age:"3h ago"},
    {section:"SPAIN",title:"El Rey pide que la UE afronte unida las amenazas “vengan de donde vengan”",deck:"The Spain section now uses the dedicated El País España feed.",source:"El País",age:"2h ago"}
  ]
};

const sections = ["Home","Washington","National","Dining","Milwaukee","Packers","Brewers","F1","Spain"];

document.getElementById("issueDate").textContent = edition.date;

const sectionNav = document.getElementById("sectionNav");
sections.forEach((name,i)=>{
  const b=document.createElement("button");
  b.textContent=name;
  if(i===0)b.classList.add("active");
  b.onclick=()=>{
    sectionNav.querySelectorAll("button").forEach(x=>x.classList.remove("active"));
    b.classList.add("active");
    if(name==="Home"){window.scrollTo({top:0,behavior:"smooth"});return}
    const el=[...document.querySelectorAll(".story-card")].find(card=>card.dataset.section===name.toUpperCase());
    el?.scrollIntoView({behavior:"smooth",block:"start"});
  };
  sectionNav.appendChild(b);
});

document.getElementById("lead").innerHTML = `
  <div class="lead-copy">
    <div class="kicker">${edition.lead.section}</div>
    <h2>${edition.lead.title}</h2>
    <p class="deck">${edition.lead.deck}</p>
    <div class="meta"><span>${edition.lead.source}</span><span>•</span><span>${edition.lead.age}</span><span>•</span><span>Save</span></div>
  </div>
  <div class="lead-art" role="img" aria-label="Editorial illustration of Washington">
    <div class="art-caption">Morning Edition · Washington</div>
  </div>
`;

document.getElementById("briefing").innerHTML = `
  <div class="kicker">BRIEFING</div>
  <h2>Your Morning Brief</h2>
  <p>A compact look at what matters most across your personal beats.</p>
  <div class="briefing-list">${edition.briefing.map(x=>`<div>${x}</div>`).join("")}</div>
`;

const grid=document.getElementById("sectionGrid");
const tpl=document.getElementById("storyCardTemplate");
edition.stories.forEach(story=>{
  const node=tpl.content.cloneNode(true);
  const card=node.querySelector(".story-card");
  card.dataset.section=story.section;
  node.querySelector(".story-kicker").textContent=story.section;
  node.querySelector("h3").textContent=story.title;
  node.querySelector(".story-deck").textContent=story.deck;
  node.querySelector(".story-meta").textContent=`${story.source} · ${story.age}`;
  grid.appendChild(node);
});

document.querySelectorAll(".bottom-nav button").forEach(btn=>{
  btn.addEventListener("click",()=>{
    document.querySelectorAll(".bottom-nav button").forEach(x=>x.classList.remove("active"));
    btn.classList.add("active");
    if(btn.dataset.view==="home")window.scrollTo({top:0,behavior:"smooth"});
  });
});
