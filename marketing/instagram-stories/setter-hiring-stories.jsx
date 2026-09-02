/* =============================================================================
   Instagram Story generator — "We're hiring setters" (US / sales exp / OTE $5-7K)
   Adobe Photoshop ExtendScript (.jsx)

   HOW TO RUN
     Photoshop -> File -> Scripts -> Browse... -> pick this file.
     (Or drop it in <Photoshop>/Presets/Scripts/ and it shows up in that menu.)

   WHAT IT DOES
     Builds four 1080x1920 story slides, each as its own document with live,
     editable text layers, then exports PNGs into CONFIG.outputFolder.

       01  hook   headline + cobalt "Remote / OTE" pill
       02  role   what the agency does and what the setter actually does
       03  fit    numbered rows: US-based / has sold before / hours / pay
       04  cta    headline + cobalt DM panel (a plain text DM, nothing else)

   Edit the CONFIG and SLIDES blocks only. Everything below "ENGINE" is
   generic layout machinery.

   Line breaks are manual on purpose: each line is its own text layer, which is
   what lets a highlight bar sit behind exactly the lines you choose.
   ============================================================================= */

#target photoshop
app.bringToFront();

/* ============================== CONFIG ==================================== */

var CONFIG = {

  brand: "Atlas Reach",              // referenced by the copy below

  // "solo"   = one slide carrying everything (default)
  // "series" = the four-slide set
  // "both"   = the single slide plus the series
  build: "solo",

  // Where the PNGs land. "~/..." resolves on both Mac and Windows.
  outputFolder: "~/Desktop/setter-stories",

  // OPTIONAL: folder of background photos (jpg/png). Used in alphabetical
  // order, one per slide, blurred + darkened. Leave "" for flat near-black.
  backgroundFolder: "",

  saveAsPsd: false,                  // also write a layered .psd per slide
  closeAfterExport: false,           // false = leave docs open for tweaking

  width: 1080,
  height: 1920,
  resolution: 72,                    // keep 72 so 1pt == 1px for font sizes

  // Atlas Reach palette. Cobalt is the highlight because deep navy on a dark
  // background has no contrast; navy carries the chrome instead.
  color: {
    accent: "2B62E0",                // --brand-blue, the cobalt highlight
    accentLift: "6D95F2",            // lifted cobalt for small text on dark
    white:  "FFFFFF",
    ink:    "0A1022",                // --navy-950, flat background w/o a photo
    chip:   "0F2147",                // --brand-navy, chip inside the cobalt panel
    dot:    "142457"                 // --navy-800, numbered circle on the rows
  },

  // Deeper, more saturated alternative (swap in if you want less pop):
  //   accent: "1C3178"  (--navy-700)   chip: "0A1022"   dot: "0F2147"

  background: {
    blur: 16,                        // gaussian blur radius on the photo, px
    darken: 62,                      // overlay opacity, 0-100
    tint: "0A1022"                   // overlay colour — navy, not pure black,
                                     // so photos take on the brand cast
  },

  // First font found on this machine wins. Add your own PostScript names.
  font: {
    heavy: ["HelveticaNeue-Bold", "Helvetica-Bold", "Inter-Bold",
            "Poppins-Bold", "Montserrat-Bold", "Arial-BoldMT", "Arial-Black"],
    body:  ["HelveticaNeue-Medium", "Helvetica", "Inter-Medium",
            "Poppins-Medium", "Montserrat-Medium", "ArialMT"]
  },

  // Instagram chrome covers roughly the top 250px and bottom 300px.
  safe: { top: 300, bottom: 1620, left: 96, right: 96 }
};

/* ============================ COPY / SLIDES ===============================
   Escapes keep the file ASCII-safe:
     ’ = ’    — = —    – = –    “ ” = “ ”
   ========================================================================== */

var SLIDES = [

  /* ---- 01 HOOK ---------------------------------------------------------- */
  {
    file: "01-hook",
    type: "hook",
    headline: "Setter seats\rare open.",       // \r = hard line break
    headlineSize: 132,
    pill: "Commission only \u00b7 OTE $5K\u2013$7K/mo.",
    pillSize: 58,
    anchor: "middle"                             // top | middle | bottom
  },

  /* ---- 02 THE ROLE ------------------------------------------------------- */
  {
    file: "02-role",
    type: "stack",
    title: "The role",
    titleSize: 108,
    rule: true,                                  // short cobalt underline
    bodySize: 46,
    blocks: [
      { lines: [
          { t: CONFIG.brand + " sells growth systems" },
          { t: "to home-service contractors" },
          { t: "across the US." }
      ]},
      { lines: [
          { t: "This is a cold outreach seat." },
          { t: "You work lists we provide \u2014 DMs," },
          { t: "calls and texts \u2014 start the" },
          { t: "conversation, and book the" },
          { t: "qualified ones." }
      ]},
      { lines: [
          { t: "Lead lists, scripts and the CRM", hl: true },
          { t: "come with the seat.", hl: true },
          { t: "You bring the volume." }
      ]}
    ]
  },

  /* ---- 03 WHAT WE NEED --------------------------------------------------- */
  {
    file: "03-fit",
    type: "rows",
    title: "What we need",
    titleSize: 104,
    rule: true,
    rowSize: 52,
    rows: [
      { icon: "01", lines: [ { t: "Based in the US." } ] },
      { icon: "02", lines: [ { t: "You\u2019ve sold", hl: true },
                             { t: "something before.", hl: true } ] },
      { icon: "03", lines: [ { t: "Cold outreach, weekdays" },
                             { t: "10am\u20135pm." } ] },
      { icon: "04", lines: [ { t: "Commission only.", hl: true },
                             { t: "OTE $5K\u2013$7K/mo." } ] }
    ]
  },

  /* ---- 04 CTA ------------------------------------------------------------ */
  {
    file: "04-cta",
    type: "cta",
    headline: "We interview\rsame day.",
    headlineSize: 132,
    rule: true,                                  // cobalt underline under headline
    blockSize: 54,
    blockTop: 1120,                              // Y of the cobalt panel's first line
    // Each line is a list of parts. chip:true draws a navy box behind that part.
    block: [
      [ { t: "DM the word " }, { t: "SETTER", chip: true } ],
      [ { t: "and where you\u2019ve sold before." } ],
      [ { t: "That\u2019s the whole application." } ]
    ]
  }
];

/* ---- SINGLE SLIDE: everything on one canvas ----------------------------
   Used when CONFIG.build is "solo" (the default) or "both". The layout
   measures itself and centres the whole stack inside the safe band, so
   editing any line here reflows the slide rather than breaking it. */

var SOLO = {
  file: "setter-hiring",
  type: "solo",

  eyebrow: "NOW HIRING \u00b7 COLD OUTREACH \u00b7 US",
  headline: "Setter seats\rare open.",
  headlineSize: 100,

  pay: "Commission only \u00b7 OTE $5K\u2013$7K/mo.",
  paySize: 46,

  roleSize: 40,
  role: [
    { t: CONFIG.brand + " sells growth systems to" },
    { t: "home-service contractors. You run the" },
    { t: "cold outreach that books the calls." }
  ],

  rowSize: 40,
  rows: [
    { t: "Based in the US." },
    { t: "Prior sales experience.", hl: true },
    { t: "Cold DMs, calls and texts, daily." },
    { t: "Lead lists, scripts and CRM provided." }
  ],

  ctaSize: 46,
  cta: [
    [ { t: "DM the word " }, { t: "SETTER", chip: true } ],
    [ { t: "and where you\u2019ve sold before." } ]
  ]
};

/* =============================== ENGINE ===================================
   Layout machinery. Edit above this line for copy and colour changes.
   ========================================================================== */

var PREFS = { ruler: app.preferences.rulerUnits, type: app.preferences.typeUnits };
app.preferences.rulerUnits = Units.PIXELS;
app.preferences.typeUnits  = TypeUnits.PIXELS;

var FONT = { heavy: resolveFont(CONFIG.font.heavy), body: resolveFont(CONFIG.font.body) };
var COL  = CONFIG.width - CONFIG.safe.left - CONFIG.safe.right;   // usable width
var BGS  = collectBackgrounds();
var OUT  = new Folder(CONFIG.outputFolder);
if (!OUT.exists) OUT.create();

var BG_TOP = null;      // topmost background layer of the doc being built
var BG_COUNT = 0;       // how many layers the background stack occupies

try {
  var QUEUE = CONFIG.build === "series" ? SLIDES
            : CONFIG.build === "both"   ? [SOLO].concat(SLIDES)
            : [SOLO];

  var made = [];
  for (var i = 0; i < QUEUE.length; i++) {
    var doc = buildSlide(QUEUE[i], i);
    made.push(exportDoc(doc, QUEUE[i].file));
    if (CONFIG.closeAfterExport) doc.close(SaveOptions.DONOTSAVECHANGES);
  }
  alert("Done — " + made.length + " stories exported to:\n" + OUT.fsName +
        "\n\nFonts used:\n" + FONT.heavy + "\n" + FONT.body);
} catch (e) {
  alert("Script stopped:\n" + e + "\nLine " + e.line);
} finally {
  app.preferences.rulerUnits = PREFS.ruler;
  app.preferences.typeUnits  = PREFS.type;
}

/* ---------------------------- slide builders ----------------------------- */

function buildSlide(spec, index) {
  var doc = newDoc(spec.file);
  BG_TOP = paintBackground(doc, index);
  BG_COUNT = doc.artLayers.length;      // everything added after this is content

  if (spec.type === "solo")  layoutSolo(doc, spec);
  if (spec.type === "hook")  layoutHook(doc, spec);
  if (spec.type === "stack") layoutStack(doc, spec);
  if (spec.type === "rows")  layoutRows(doc, spec);
  if (spec.type === "cta")   layoutCta(doc, spec);

  return doc;
}

/* ONE SLIDE - eyebrow, headline, pay pill, role, checklist rows, CTA panel.
   Everything is laid out top-down from a cursor, then the whole stack is
   centred in the safe band so it can never crowd the phone's bottom UI. */
function layoutSolo(doc, s) {
  var x = CONFIG.safe.left;
  var y = CONFIG.safe.top;

  // eyebrow
  var eb = addText(doc, s.eyebrow, {
    size: 30, tracking: 180, font: FONT.body,
    color: CONFIG.color.accentLift, name: "eyebrow"
  });
  fitText(eb, COL, 20, 1);
  moveTo(eb, x, y, "left");
  y = bottom(eb) + 26;

  // headline
  var head = addText(doc, s.headline, {
    size: s.headlineSize, leading: s.headlineSize * 0.92, tracking: -28, name: "headline"
  });
  fitText(head, COL, 48, 0.92);
  moveTo(head, x, y, "left");
  y = bottom(head) + 30;

  // pay pill
  var pill = addText(doc, s.pay, { size: s.paySize, tracking: -8, name: "pay" });
  fitText(pill, COL - 60, 26, 1.05);
  moveTo(pill, x + 22, y + 16, "left");
  var pillBar = highlight(doc, pill, CONFIG.color.accent, 22, 14);
  y = bottom(pillBar) + 44;

  // role
  var roleLead = s.roleSize * 1.30;
  for (var r = 0; r < s.role.length; r++) {
    var RL = addText(doc, s.role[r].t, {
      size: s.roleSize, leading: roleLead, tracking: -8, font: FONT.body, name: "role"
    });
    fitText(RL, COL, 24, 1.30);
    moveTo(RL, x, y, "left");
    y += roleLead;
  }
  y += 24;

  // checklist rows, small cobalt square instead of the series' numbered circle
  var rowLead = s.rowSize * 1.40;
  for (var i = 0; i < s.rows.length; i++) {
    var row = s.rows[i];
    var RT = addText(doc, row.t, {
      size: s.rowSize, leading: rowLead, tracking: -8, name: "row " + (i + 1)
    });
    fitText(RT, COL - 46, 24, 1.40);
    moveTo(RT, x + 46, y, "left");
    if (row.hl) highlight(doc, RT, CONFIG.color.accent, 14, 9);
    var b = bounds(RT);
    drawRect(doc, x, b.t + (b.h - 16) / 2, 16, 16,
             row.hl ? CONFIG.color.white : CONFIG.color.accent, "bullet");
    y += rowLead;
  }
  y += 34;

  ctaPanel(doc, s.cta, s.ctaSize, y);
  centreContent(doc);
}

/* Cobalt panel with centred lines; chip:true boxes a single part in navy.
   Returns the panel's bottom edge. */
function ctaPanel(doc, block, size, topY) {
  var lead = size * 1.28;
  var mid = CONFIG.width / 2;
  var maxW = COL - 90;
  var baseline = topY + size;
  var box = { l: 1e9, t: 1e9, r: -1e9, b: -1e9 };
  var chips = [];

  for (var i = 0; i < block.length; i++) {
    var line = layoutParts(doc, block[i], size, maxW, mid, baseline);
    for (var j = 0; j < line.layers.length; j++) {
      var bb = bounds(line.layers[j]);
      box.l = Math.min(box.l, bb.l); box.t = Math.min(box.t, bb.t);
      box.r = Math.max(box.r, bb.r); box.b = Math.max(box.b, bb.b);
      if (block[i][j].chip) chips.push(line.layers[j]);
    }
    baseline += lead;
  }

  for (var c = 0; c < chips.length; c++) {
    highlight(doc, chips[c], CONFIG.color.chip, 14, 8);
  }

  var padX = 46, padY = 38;
  var panel = drawRect(doc, box.l - padX, box.t - padY,
                       (box.r - box.l) + padX * 2, (box.b - box.t) + padY * 2,
                       CONFIG.color.accent, "cta panel");
  if (BG_TOP) panel.move(BG_TOP, ElementPlacement.PLACEBEFORE);
  return box.b + padY;
}

/* Every layer sitting above the background stack. Counted rather than compared
   by identity: ExtendScript hands back a fresh wrapper on each property read,
   so `layer === BG_TOP` is not dependable. */
function contentLayers(doc) {
  var out = [];
  var n = doc.artLayers.length - BG_COUNT;
  for (var i = 0; i < n; i++) out.push(doc.artLayers[i]);
  return out;
}

/* Shifts the whole composition so it sits centred between the safe margins.
   This is what keeps the copy off the phone's bottom UI no matter how much
   you add or remove above. */
function centreContent(doc) {
  var ls = contentLayers(doc);
  if (!ls.length) return;
  var t = 1e9, b = -1e9;
  for (var i = 0; i < ls.length; i++) {
    var bb = bounds(ls[i]);
    t = Math.min(t, bb.t); b = Math.max(b, bb.b);
  }
  var band = CONFIG.safe.bottom - CONFIG.safe.top;
  var dy = CONFIG.safe.top + (band - (b - t)) / 2 - t;
  for (var j = 0; j < ls.length; j++) ls[j].translate(0, dy);
}

/* 01 - giant headline, vertically anchored, cobalt pill underneath */
function layoutHook(doc, s) {
  var head = addText(doc, s.headline, {
    size: s.headlineSize, leading: s.headlineSize * 0.92, tracking: -30, name: "headline"
  });
  fitText(head, COL, 60, 0.92);

  var pill = addText(doc, s.pill, {
    size: s.pillSize, leading: s.pillSize * 1.05, tracking: -10, name: "OTE pill"
  });
  fitText(pill, COL - 60, 30, 1.05);

  var gap = 40;
  var total = h(head) + gap + h(pill) + 40;          // 40 = pill vertical padding
  var top = anchorTop(s.anchor, total);

  moveTo(head, CONFIG.safe.left, top, "left");
  moveTo(pill, CONFIG.safe.left + 26, bottom(head) + gap + 20, "left");
  highlight(doc, pill, CONFIG.color.accent, 26, 18);
}

/* 02 - title + cobalt rule + stacked body blocks with per-line highlights */
function layoutStack(doc, s) {
  var y = CONFIG.safe.top + 40;

  var title = addText(doc, s.title, { size: s.titleSize, tracking: -25, name: "title" });
  fitText(title, COL, 48, 1);
  moveTo(title, CONFIG.safe.left, y, "left");
  y = bottom(title) + 24;

  if (s.rule) y = drawRule(doc, CONFIG.safe.left, y, 130, 12) + 46;

  var lead = s.bodySize * 1.30;
  for (var b = 0; b < s.blocks.length; b++) {
    var lines = s.blocks[b].lines;
    for (var i = 0; i < lines.length; i++) {
      var L = addText(doc, lines[i].t, {
        size: s.bodySize, leading: lead, tracking: -8,
        font: lines[i].hl ? FONT.heavy : FONT.body, name: "body"
      });
      fitText(L, COL, 24, 1.30);
      moveTo(L, CONFIG.safe.left, y, "left");
      if (lines[i].hl) highlight(doc, L, CONFIG.color.accent, 14, 10);
      y += lead;
    }
    y += s.bodySize * 0.75;                          // gap between blocks
  }
}

/* 03 - title + cobalt rule + numbered rows */
function layoutRows(doc, s) {
  var y = CONFIG.safe.top + 30;

  var title = addText(doc, s.title, { size: s.titleSize, tracking: -28, name: "title" });
  fitText(title, COL, 48, 1);
  moveTo(title, CONFIG.safe.left, y, "left");
  y = bottom(title) + 22;

  if (s.rule) y = drawRule(doc, CONFIG.safe.left, y, 175, 13) + 70;

  var dot = 96, gutter = 34;
  var textX = CONFIG.safe.left + dot + gutter;
  var textW = CONFIG.width - CONFIG.safe.right - textX;
  var lead  = s.rowSize * 1.24;

  for (var r = 0; r < s.rows.length; r++) {
    var row = s.rows[r];
    var rowTop = y, lineY = y;

    for (var i = 0; i < row.lines.length; i++) {
      var L = addText(doc, row.lines[i].t, {
        size: s.rowSize, leading: lead, tracking: -10, name: "row " + (r + 1)
      });
      fitText(L, textW, 26, 1.24);
      moveTo(L, textX, lineY, "left");
      if (row.lines[i].hl) highlight(doc, L, CONFIG.color.accent, 16, 12);
      lineY += lead;
    }

    var rowH = lineY - rowTop;
    drawNumberDot(doc, CONFIG.safe.left, rowTop + (rowH - dot) / 2, dot, row.icon, s.rowSize);
    y = rowTop + Math.max(rowH, dot) + 62;           // gap between rows
  }
}

/* 04 - headline + cobalt rule + centred cobalt CTA panel with a navy chip */
function layoutCta(doc, s) {
  var head = addText(doc, s.headline, {
    size: s.headlineSize, leading: s.headlineSize * 0.92, tracking: -30, name: "headline"
  });
  fitText(head, COL, 60, 0.92);
  moveTo(head, CONFIG.safe.left, CONFIG.safe.top + 180, "left");

  if (s.rule) drawRule(doc, CONFIG.safe.left + 8, bottom(head) + 14, w(head) * 0.62, 14);

  ctaPanel(doc, s.block, s.blockSize, s.blockTop);
}

/* Lays a row of text parts side by side, centred on midX, sharing one baseline.
   Shrinks the whole row until it fits maxW. Returns { layers, width }. */
function layoutParts(doc, parts, size, maxW, midX, baseline) {
  var layers, widths, total, guard = 0;

  while (true) {
    layers = []; widths = []; total = 0;
    for (var i = 0; i < parts.length; i++) {
      var L = addText(doc, parts[i].t, {
        size: size, leading: size * 1.28, tracking: -8,
        name: "cta " + parts[i].t.substr(0, 14)
      });
      layers.push(L);
      var wid = w(L);
      widths.push(wid);
      total += wid;
    }
    if (total <= maxW || size <= 26 || guard++ > 40) break;
    for (var k = 0; k < layers.length; k++) layers[k].remove();
    size -= 2;
  }

  var x = midX - total / 2;
  for (var p = 0; p < layers.length; p++) {
    layers[p].textItem.position = [x, baseline];     // position == baseline anchor
    x += widths[p];
  }
  return { layers: layers, width: total };
}

/* ------------------------------ primitives -------------------------------- */

function newDoc(name) {
  var d = app.documents.add(CONFIG.width, CONFIG.height, CONFIG.resolution,
                            name, NewDocumentMode.RGB, DocumentFill.WHITE);
  app.activeDocument = d;
  d.selection.selectAll();
  d.selection.fill(solid(CONFIG.color.ink));
  d.selection.deselect();
  return d;
}

/* Returns the topmost background layer, so content can be stacked above it. */
function paintBackground(doc, index) {
  var topLayer = doc.artLayers[doc.artLayers.length - 1];

  if (BGS.length) {
    var f = BGS[index % BGS.length];
    try {
      var src = app.open(f);
      src.flatten();
      src.selection.selectAll();
      src.selection.copy();
      src.close(SaveOptions.DONOTSAVECHANGES);
      app.activeDocument = doc;
      var photo = doc.paste();
      photo.name = "photo";
      var bb = bounds(photo);
      var scale = Math.max(CONFIG.width / bb.w, CONFIG.height / bb.h) * 100;
      photo.resize(scale, scale, AnchorPosition.MIDDLECENTER);
      if (CONFIG.background.blur > 0) photo.applyGaussianBlur(CONFIG.background.blur);
      topLayer = photo;
    } catch (e) { /* unreadable file: keep the flat fill */ }
  }

  if (CONFIG.background.darken > 0) {
    var shade = drawRect(doc, 0, 0, CONFIG.width, CONFIG.height, CONFIG.background.tint, "darken");
    shade.opacity = CONFIG.background.darken;
    topLayer = shade;
  }
  return topLayer;
}

function addText(doc, str, o) {
  o = o || {};
  var layer = doc.artLayers.add();
  layer.kind = LayerKind.TEXT;
  layer.name = o.name || "text";
  var t = layer.textItem;
  t.font = o.font || FONT.heavy;
  t.size = o.size || 60;
  t.color = solid(o.color || CONFIG.color.white);
  t.justification = o.justification || Justification.LEFT;
  if (o.tracking !== undefined) t.tracking = o.tracking;
  if (o.leading) { t.useAutoLeading = false; t.leading = o.leading; }
  t.position = [CONFIG.safe.left, 400];
  t.contents = str;
  return layer;
}

/* Shrinks a layer until it fits maxW, keeping leading proportional. */
function fitText(layer, maxW, minSize, leadRatio) {
  var t = layer.textItem, size = asPx(t.size), guard = 0;
  while (w(layer) > maxW && size > (minSize || 24) && guard++ < 200) {
    size -= 2;
    t.size = size;
    if (!t.useAutoLeading && leadRatio) t.leading = size * leadRatio;
  }
  return layer;
}

/* Coloured bar behind a text layer — the highlight-bar look. */
function highlight(doc, layer, hex, padX, padY) {
  var b = bounds(layer);
  var rect = drawRect(doc, b.l - padX, b.t - padY,
                      b.w + padX * 2, b.h + padY * 2, hex, "highlight");
  rect.move(layer, ElementPlacement.PLACEAFTER);     // PLACEAFTER == below
  return rect;
}

/* Short thick accent rule under a title. Returns its bottom Y. */
function drawRule(doc, x, y, width, thickness) {
  drawRect(doc, x, y, width, thickness, CONFIG.color.accent, "rule");
  return y + thickness;
}

/* Numbered circle for the requirements rows. */
function drawNumberDot(doc, x, y, size, label, textSize) {
  var circle = doc.artLayers.add();
  circle.name = "dot " + label;
  selectEllipse(doc, x, y, size, size);
  doc.selection.fill(solid(CONFIG.color.dot));
  doc.selection.deselect();

  var n = addText(doc, label, { size: Math.round(textSize * 0.60), tracking: 20,
                                name: "dot label " + label });
  var b = bounds(n);
  n.translate(x + size / 2 - (b.l + b.w / 2), y + size / 2 - (b.t + b.h / 2));
}

function drawRect(doc, x, y, width, height, hex, name) {
  var layer = doc.artLayers.add();
  layer.name = name || "rect";
  x = Math.round(x); y = Math.round(y);
  width = Math.round(width); height = Math.round(height);
  doc.selection.select([[x, y], [x + width, y], [x + width, y + height], [x, y + height]]);
  doc.selection.fill(solid(hex));
  doc.selection.deselect();
  return layer;
}

/* Photoshop's DOM has no ellipse selection, so go through Action Manager. */
function selectEllipse(doc, x, y, width, height) {
  var d = new ActionDescriptor();
  var ref = new ActionReference();
  ref.putProperty(charIDToTypeID("Chnl"), charIDToTypeID("fsel"));
  d.putReference(charIDToTypeID("null"), ref);
  var e = new ActionDescriptor();
  e.putUnitDouble(charIDToTypeID("Top "), charIDToTypeID("#Pxl"), y);
  e.putUnitDouble(charIDToTypeID("Left"), charIDToTypeID("#Pxl"), x);
  e.putUnitDouble(charIDToTypeID("Btom"), charIDToTypeID("#Pxl"), y + height);
  e.putUnitDouble(charIDToTypeID("Rght"), charIDToTypeID("#Pxl"), x + width);
  d.putObject(charIDToTypeID("T   "), charIDToTypeID("Elps"), e);
  executeAction(charIDToTypeID("setd"), d, DialogModes.NO);
}

/* ------------------------------- helpers ---------------------------------- */

function solid(hex) { var c = new SolidColor(); c.rgb.hexValue = hex; return c; }
function asPx(v)    { return (v && typeof v === "object" && v.as) ? v.as("px") : v; }

function bounds(layer) {
  var b = layer.bounds;
  var l = asPx(b[0]), t = asPx(b[1]), r = asPx(b[2]), bo = asPx(b[3]);
  return { l: l, t: t, r: r, b: bo, w: r - l, h: bo - t };
}
function w(layer)      { return bounds(layer).w; }
function h(layer)      { return bounds(layer).h; }
function bottom(layer) { return bounds(layer).b; }

/* align: left | center | right (horizontal); y is always the top edge. */
function moveTo(layer, x, y, align) {
  var b = bounds(layer), dx;
  if (align === "center")     dx = x - (b.l + b.w / 2);
  else if (align === "right") dx = x - b.r;
  else                        dx = x - b.l;
  layer.translate(dx, y - b.t);
  return layer;
}

function anchorTop(anchor, blockHeight) {
  if (anchor === "top")    return CONFIG.safe.top;
  if (anchor === "bottom") return CONFIG.safe.bottom - blockHeight;
  return (CONFIG.height - blockHeight) / 2;
}

function resolveFont(candidates) {
  for (var i = 0; i < candidates.length; i++) {
    for (var f = 0; f < app.fonts.length; f++) {
      if (app.fonts[f].postScriptName === candidates[i]) return candidates[i];
    }
  }
  return app.fonts[0].postScriptName;                // last resort
}

function collectBackgrounds() {
  if (!CONFIG.backgroundFolder) return [];
  var folder = new Folder(CONFIG.backgroundFolder);
  if (!folder.exists) return [];
  var files = folder.getFiles(/\.(jpg|jpeg|png|tif|tiff|psd)$/i);
  files.sort(function (a, b) { return a.name > b.name ? 1 : -1; });
  return files;
}

function exportDoc(doc, name) {
  var png = new File(OUT.fsName + "/" + name + ".png");
  var opts = new PNGSaveOptions();
  opts.compression = 6;
  opts.interlaced = false;
  doc.saveAs(png, opts, true, Extension.LOWERCASE);
  if (CONFIG.saveAsPsd) {
    var psd = new File(OUT.fsName + "/" + name + ".psd");
    doc.saveAs(psd, new PhotoshopSaveOptions(), true, Extension.LOWERCASE);
  }
  return png.fsName;
}
