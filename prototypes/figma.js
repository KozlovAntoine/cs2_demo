const figPages = {library:'Vidéothèque', export:'Importation', zones:'Callouts', components:'Galeries'};
const figIcons = {library:'▦', export:'↥', zones:'◫', components:'▱'};
let figFiles = ['demo_inferno.dem', 'match_hltv.rar'];
const figKinds = new Set(['Smoke', 'Flash']);
let figLimit = 12;
let figJobs = [
  {name:'demo_mirage.dem', map:'Mirage · Smoke', count:8, max:12, status:'running'},
  {name:'demo_dust2.dem', map:'Dust II · Smoke, Flash', count:3, max:12, status:'paused'},
  {name:'demo_inferno_01.dem', map:'Inferno · Molotov', count:8, max:8, status:'done'}
];
let figZonesSaved = false;

function figHeading(title, description, extra = '') {
  return `<div class="fig-heading"><div><h1>${title}</h1><p>${description}</p></div>${extra}</div>`;
}

function renderFigma() {
  document.body.className = 'theme-figma';
  const activeJob = figJobs.find(job => job.status === 'running') ?? figJobs.find(job => job.status === 'paused');
  const action = {
    library:primary('↥　Importer .dem ou archive .rar','data-go="export"'),
    export:primary('›　 Ouvrir la vidéothèque','data-go="library"'),
    zones:primary('✓　 Enregistrer les zones','data-fig-save'),
    components:primary('↥　 Importation','data-go="export"')
  }[state.page];
  const route = (page,label,icon) => `<a href="#v=f&p=${page}" ${page===state.page?'aria-current="page"':''}><span aria-hidden="true">${icon}</span>${label}</a>`;
  app.innerHTML = `<div class="fig-shell">
    <aside class="fig-sidebar">
      <a class="fig-brand" href="#v=f&p=library"><strong>CS2</strong> UTILITIES</a>
      <span class="eyebrow">VOTRE TERRAIN DE JEU</span>
      <nav aria-label="Navigation">${route('library','Vidéothèque',figIcons.library)}
        <button type="button" data-fig-favorites><span aria-hidden="true">☆</span> Favoris</button>
        ${['zones','components','export'].map(page=>route(page,figPages[page],figIcons[page])).join('')}
      </nav>
      <div class="fig-sidebar-maps"><span class="eyebrow">CARTES</span><a href="#v=f&p=library">Ancient <small>${clips.length}</small></a></div>
      ${activeJob ? `<div class="fig-job-mini"><span class="eyebrow">${activeJob.status==='paused'?'EN PAUSE':'GÉNÉRATION EN COURS'}</span><strong>${escapeHTML(activeJob.name)}</strong><small>${activeJob.count} / ${activeJob.max} clips</small>
        <progress max="${activeJob.max}" value="${activeJob.count}" aria-label="Progression"></progress>
        ${btn(activeJob.status==='paused'?'▷ Reprendre':'Ⅱ Pause',`data-fig-job="${figJobs.indexOf(activeJob)}" data-operation="toggle"`)}</div>` : ''}
      <div class="fig-local"><span>●</span> Application prête <small>En local · Données d’exemple</small><a href="#">← Tous les prototypes</a></div>
    </aside>
    <div class="fig-main">
      <header class="fig-top"><div>Espace personnel <span>›</span> <strong>${figPages[state.page]}</strong></div><div><small>Prototype local</small>${action}</div></header>
      <main class="fig-page">${({library:figLibrary,export:figImport,zones:figZoneEditor,components:figGalleries})[state.page]()}</main>
    </div>
  </div>`;
  bind();
  bindFigma();
}

function figLibrary() {
  const items = filtered();
  if (items.length && !items.some(clip => clip.id === state.selected)) state.selected = items[0].id;
  const clip = selectedClip();
  return `<div class="fig-library">
    <div class="fig-library-primary">
      ${figHeading('Votre prochain lancer décisif.','Explorez, apprenez et retrouvez vos repères en jeu.')}
      <div class="fig-map-tabs"><span>Ancient · ${clips.length}</span><small>${items.length} clips · 1 carte</small></div>
      <div class="fig-filters"><label class="sr-only" for="fig-search">Rechercher un callout</label><input id="fig-search" type="text" placeholder="Rechercher un callout…" value="${escapeHTML(state.search || '')}">${filters()}</div>
      ${items.length ? `<div class="fig-player">${videoPanel(clip,false)}</div>${clipList(items)}` : `<div class="empty-state"><h2>Aucun lancer</h2>${btn('Réinitialiser les filtres','data-action="reset"')}</div>`}
    </div>
    ${items.length ? `<aside class="fig-library-detail">
      <div class="fig-detail-heading"><h2>Le lancer, en détail</h2><small>ANCIENT</small></div>
      ${radarPanel()}
      <section class="fig-card">
        <h2>Reproduire ce lancer</h2>
        <span class="eyebrow">CALLOUT</span>
        <div class="fig-lineup-title"><strong>${escapeHTML(clip.title)}</strong>${btn('Renommer','data-action="rename"','subtle')}</div>
        ${throwControls(clip.lineup?.controls)}
        ${consolePanel(clip)}
      </section>
      <div class="fig-note"><h2>Un repère, puis le bon geste.</h2><p>Retrouvez le départ sur le radar, puis répétez le lancer en jeu.</p></div>
    </aside>` : ''}
  </div>`;
}

function figImport() {
  return `${figHeading('De la démo au prochain déclic.','Importez vos parties. Gardez les lancers qui font la différence.','<span class="fig-steps"><b>01 IMPORTER</b><span>02 GÉNÉRER</span><span>03 APPRENDRE</span></span>')}
  <div class="fig-import-grid">
    <section class="fig-card">
      <div class="fig-row"><h2>Vos démos</h2><small>${figFiles.length} fichiers prêts</small></div>
      <label class="upload-zone fig-drop" for="demo-file"><span aria-hidden="true" class="fig-upload-icon">↥</span><span><strong>Glissez vos démos ou archives HLTV ici</strong><small>Fichiers .dem ou .rar · traitement entièrement local</small><span class="button-look">Parcourir les fichiers</span><input id="demo-file" type="file" accept=".dem,.rar" multiple><span id="file-label" class="sr-only"></span></span></label>
      <ul class="fig-file-list">${figFiles.map((name,i)=>`<li><span aria-hidden="true">▤</span><div>${escapeHTML(name)}<small>${name.toLowerCase().endsWith('.rar')?'Archive HLTV':'Démo CS2'}</small></div><span class="fig-ready">Prêt</span>${btn('×',`data-fig-remove="${i}" aria-label="Retirer ${escapeHTML(name)}"`,'subtle')}</li>`).join('')}</ul>
    </section>
    <section class="fig-card">
      <h2>Réglages de génération</h2>
      <p class="fig-muted">Types de grenades</p>
      <div class="fig-kinds">${['Smoke','Flash','Molotov','HE','Leurre'].map(kind=>btn(kind,`data-fig-kind="${kind}" aria-pressed="${figKinds.has(kind)}"`)).join('')}</div>
      <div class="fig-row"><label for="fig-limit">Nombre maximal de clips</label><input id="fig-limit" type="number" value="${figLimit}" min="1" max="999"></div>
      <p class="fig-muted">Par démo · les doublons sont ignorés automatiquement.</p>
      ${primary(`Ajouter ${figFiles.length} ${figFiles.length===1?'fichier':'fichiers'} à la file　→`,`data-fig-queue ${!figFiles.length||!figKinds.size?'disabled':''}`)}
    </section>
  </div>
  <section class="fig-card fig-queue">
    <h2>File de traitement</h2>
    <table><thead><tr><th>Source</th><th>État</th><th>Clips</th><th>Progression</th><th>Actions</th></tr></thead>
      <tbody>${figJobs.map(figJobRow).join('')}</tbody></table>
  </section>
  <div class="fig-note fig-row"><p>Les démos sont analysées sur votre ordinateur.</p>${btn('Ouvrir les galeries précédentes ↗','data-go="components"','subtle')}</div>`;
}

function figJobRow(job, i) {
  const labels = {running:'Génération en cours',paused:'En pause',done:'Terminée',cancelled:'Annulée'};
  const actions = ['running','paused'].includes(job.status)
    ? btn(job.status==='running'?'Ⅱ Pause':'▷ Reprendre',`data-fig-job="${i}" data-operation="toggle"`)+btn('Annuler',`data-fig-job="${i}" data-operation="cancel"`)
    : job.status==='done' ? btn('Voir les clips ↗','data-go="library"') : '';
  return `<tr class="job-${job.status}"><td>${escapeHTML(job.name)}<small>${escapeHTML(job.map)}</small></td><td>● ${labels[job.status]}</td><td>${job.count} / ${job.max}</td><td><div class="fig-progress"><progress aria-label="Progression" max="${job.max}" value="${job.count}"></progress><span>${Math.round(job.count/job.max*100)} %</span></div></td><td>${actions}</td></tr>`;
}

function figZoneEditor() {
  return `${figHeading('Donnez un nom à chaque repère.','Dessinez des zones sur la carte pour nommer automatiquement vos lancers.',`<small class="fig-accent">● ${figZonesSaved?'Zones enregistrées':'Modifications non enregistrées'}</small>`)}
  <div class="fig-zone-grid">
    <section class="fig-card fig-zone-map">
      <div class="fig-zone-toolbar"><select aria-label="Carte"><option>Ancient</option></select>${btn('⌖ Sélection','data-fig-select')}${btn('◇ Polygone','data-action="draw"')}${btn('Ajuster','data-fig-fit')}<small>${state.zoom} %</small></div>
      ${radarPanel(true)}
      <p class="fig-muted">Sélectionnez une zone pour modifier son nom ou sa couleur.</p>
    </section>
    <aside class="fig-zone-detail">
      <section class="fig-card"><div class="fig-row"><h2>Zones d’Ancient</h2>${btn('+','data-action="draw" aria-label="Nouvelle zone"')}</div>
      <div class="zone-list">${['A Main','A Site','B Site','Mid'].map((name,i)=>btn(`<i class="zone-color color-${i}" style="background:var(--zone-color-${i})"></i>${name}`,`data-zone="${name}" aria-pressed="${state.zone===name}"`)).join('')}</div></section>
      <section class="fig-card fig-zone-fields"><h2>Zone sélectionnée</h2>
        <label>Nom du callout<input id="zone-name" maxlength="64" value="${escapeHTML(state.zone)}"></label>
        <div class="fig-row fig-zone-actions">${btn('Enregistrer','data-fig-save')}${btn('Supprimer','data-action="delete-zone"','danger')}</div>
      </section>
      <section class="fig-note"><span class="eyebrow">DES ZONES AUX NOMS DE CLIPS</span><h2>Départ → Activation</h2><p>Spawn T → ${escapeHTML(state.zone)}</p></section>
    </aside>
  </div>`;
}

function figGalleries() {
  return `<div class="fig-galleries">${figHeading('Galeries','Retrouvez les clips de vos démos.')}${clipList(clips)}</div>`;
}

function bindFigma() {
  const wire = (selector, event, fn) => document.querySelectorAll(selector).forEach(el => el.addEventListener(event, fn));
  const selectLibrary = () => state.page==='library' ? render() : go('library');
  wire('[data-fig-favorites]','click',()=>{
    state.filter = state.filter==='favorites' ? 'all' : 'favorites';
    selectLibrary();
  });
  wire('#fig-search','input',({target})=>{
    const position = target.selectionStart;
    state.search = target.value;
    render();
    const next = document.querySelector('#fig-search');
    next.focus();
    next.setSelectionRange(position,position);
  });
  wire('#demo-file','change',({target})=>addFigmaFiles(target.files));
  wire('.fig-drop','drop',event=>{
    event.preventDefault();
    event.stopPropagation();
    addFigmaFiles(event.dataTransfer.files);
  });
  wire('[data-fig-remove]','click',({currentTarget})=>{
    figFiles.splice(Number(currentTarget.dataset.figRemove),1);
    render();
  });
  wire('[data-fig-kind]','click',({currentTarget})=>{
    const kind=currentTarget.dataset.figKind;
    if (figKinds.has(kind)) figKinds.delete(kind);
    else figKinds.add(kind);
    render();
  });
  wire('#fig-limit','change',({target})=>{
    figLimit = Math.min(999,Math.max(1,Math.trunc(target.valueAsNumber)||12));
    target.value = figLimit;
  });
  wire('[data-fig-queue]','click',()=>{
    figJobs.push(...figFiles.map(name=>({name,map:[...figKinds].join(', '),count:0,max:figLimit,status:'running'})));
    figFiles = [];
    render();
    notify('Fichiers ajoutés à la file de démonstration.');
  });
  wire('[data-fig-job]','click',({currentTarget})=>{
    const job = figJobs[Number(currentTarget.dataset.figJob)];
    job.status = currentTarget.dataset.operation==='cancel' ? 'cancelled' : job.status==='running' ? 'paused' : 'running';
    render();
  });
  wire('.fig-galleries .clip-card','click',()=>go('library'));
  wire('[data-fig-fit]','click',()=>{state.zoom=100;render();});
  wire('[data-fig-select]','click',()=>{state.drawing=false;render();});
  wire('[data-fig-save]','click',()=>{
    state.zone=document.querySelector('#zone-name')?.value.trim()||state.zone;
    state.drawing=false;
    figZonesSaved=true;
    render();
    notify('Zones enregistrées dans le prototype.');
  });
}

function addFigmaFiles(files) {
  const names=[...files].filter(file=>/\.(dem|rar)$/i.test(file.name)).map(file=>file.name);
  if (!names.length) return notify('Choisissez un fichier .dem ou .rar.');
  figFiles=[...new Set([...figFiles,...names])];
  render();
}
