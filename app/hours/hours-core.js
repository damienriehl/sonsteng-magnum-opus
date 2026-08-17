(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HoursLog = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var MAX_BYTES = 1024 * 1024, MAX_DEPTH = 12, MAX_STRING = 4000, MAX_ARRAY = 500;
  var HEADERS = ['record_type','entry_id','date','project','matter','activity','worked_hours','billable_hours','gap_hours','class_time','narrative','contribution_id','deliverable_id','deliverable_title','contribution_date','contribution_type','description','related_entry_ids'];
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function tenths(n) { return typeof n === 'number' && isFinite(n) && Math.abs(n * 10 - Math.round(n * 10)) < 1e-8; }
  function identifier(v) { return typeof v === 'string' && v.length > 0 && v.length <= 128 && /^[A-Za-z0-9._:-]+$/.test(v); }
  function validDate(v) {
    if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
    var d = new Date(v + 'T00:00:00Z');
    return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v;
  }
  function walk(v, depth) {
    if (depth > MAX_DEPTH) throw new Error('Import nesting is too deep.');
    if (typeof v === 'string' && v.length > MAX_STRING) throw new Error('Import contains an overlong string.');
    if (Array.isArray(v)) {
      if (v.length > MAX_ARRAY) throw new Error('Import collection is too large.');
      v.forEach(function (x) { walk(x, depth + 1); });
    } else if (v && typeof v === 'object') Object.keys(v).forEach(function (k) { walk(v[k], depth + 1); });
  }
  function strictKeys(obj, keys, path, errors) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) { errors.push({code:'invalid_object', path:path}); return; }
    Object.keys(obj).forEach(function (k) { if (keys.indexOf(k) < 0) errors.push({code:'additional_property', path:path + '.' + k}); });
  }
  function requiredText(value, path, max, errors, allowEmpty) {
    if (typeof value !== 'string' || (!allowEmpty && !value) || value.length > max) errors.push({code:'invalid_string', path:path});
  }
  function validateLog(doc) {
    var errors = [], worked = 0, billable = 0, ids = {}, contributionIds = {};
    strictKeys(doc, ['schema_version','learner_id','offering_id','week','entries','contribution_log'], '$', errors);
    if (!doc || doc.schema_version !== 1) errors.push({code:'version_mismatch', path:'schema_version'});
    requiredText(doc && doc.learner_id, 'learner_id', 128, errors, false);
    requiredText(doc && doc.offering_id, 'offering_id', 128, errors, false);
    if (doc && (!identifier(doc.learner_id) || !identifier(doc.offering_id))) errors.push({code:'invalid_identity', path:'$'});
    var week = doc && doc.week || {};
    strictKeys(week, ['start','end'], 'week', errors);
    if (!validDate(week.start) || !validDate(week.end) || week.end < week.start) errors.push({code:'invalid_week', path:'week'});
    var entries = doc && Array.isArray(doc.entries) ? doc.entries : [];
    if (!doc || !Array.isArray(doc.entries)) errors.push({code:'required', path:'entries'});
    if (entries.length > MAX_ARRAY) errors.push({code:'collection_too_large', path:'entries'});
    entries.forEach(function (e, i) {
      var p = 'entries[' + i + ']';
      strictKeys(e, ['id','date','project','matter','activity','worked_hours','billable_hours','class_time','narrative'], p, errors);
      if (ids[e.id]) errors.push({code:'duplicate_entry_id', path:p + '.id'}); ids[e.id] = true;
      requiredText(e.id, p+'.id', 128, errors, false); requiredText(e.project, p+'.project', 500, errors, false);
      if (!identifier(e.id)) errors.push({code:'invalid_identifier', path:p+'.id'});
      requiredText(e.matter, p+'.matter', 500, errors, true); requiredText(e.activity, p+'.activity', 500, errors, false);
      requiredText(e.narrative, p+'.narrative', 4000, errors, true);
      if (typeof e.class_time !== 'boolean') errors.push({code:'invalid_boolean', path:p+'.class_time'});
      if (!validDate(e.date) || e.date < week.start || e.date > week.end) errors.push({code:'date_outside_week', path:p + '.date'});
      ['worked_hours','billable_hours'].forEach(function (f) {
        if (!tenths(e[f])) errors.push({code:'hours_not_tenths', path:p + '.' + f});
        if (typeof e[f] !== 'number' || e[f] < 0) errors.push({code:'negative_hours', path:p + '.' + f});
        if (typeof e[f] === 'number' && e[f] > 168) errors.push({code:'hours_exceed_week', path:p + '.' + f});
      });
      if (typeof e.worked_hours === 'number') worked += e.worked_hours;
      if (typeof e.billable_hours === 'number') billable += e.billable_hours;
      if (e.billable_hours > e.worked_hours) errors.push({code:'billable_exceeds_worked', path:p + '.billable_hours'});
    });
    var contributions = doc && Array.isArray(doc.contribution_log) ? doc.contribution_log : [];
    if (!doc || !Array.isArray(doc.contribution_log)) errors.push({code:'required', path:'contribution_log'});
    if (contributions.length > MAX_ARRAY) errors.push({code:'collection_too_large', path:'contribution_log'});
    contributions.forEach(function (c, j) {
      var cp = 'contribution_log[' + j + ']';
      strictKeys(c, ['id','deliverable_id','deliverable_title','contribution_date','contribution_type','description','related_entry_ids'], cp, errors);
      if (contributionIds[c.id]) errors.push({code:'duplicate_contribution_id', path:cp + '.id'}); contributionIds[c.id] = true;
      if (!validDate(c.contribution_date) || c.contribution_date < week.start || c.contribution_date > week.end) errors.push({code:'date_outside_week', path:cp + '.contribution_date'});
      requiredText(c.id, cp+'.id', 128, errors, false); requiredText(c.deliverable_id, cp+'.deliverable_id', 128, errors, false);
      if (!identifier(c.id) || !identifier(c.deliverable_id)) errors.push({code:'invalid_identifier', path:cp});
      requiredText(c.deliverable_title, cp+'.deliverable_title', 500, errors, false); requiredText(c.contribution_type, cp+'.contribution_type', 500, errors, false);
      requiredText(c.description, cp+'.description', 2000, errors, false);
      if (c.related_entry_ids !== undefined && !Array.isArray(c.related_entry_ids)) errors.push({code:'invalid_array', path:cp+'.related_entry_ids'});
      if (Array.isArray(c.related_entry_ids) && c.related_entry_ids.length > 100) errors.push({code:'collection_too_large', path:cp+'.related_entry_ids'});
      var relatedSeen={}; (c.related_entry_ids || []).forEach(function (rid) { if (!identifier(rid)) errors.push({code:'invalid_identifier', path:cp+'.related_entry_ids'}); if (relatedSeen[rid]) errors.push({code:'duplicate_related_entry_id', path:cp+'.related_entry_ids'}); relatedSeen[rid]=true; if (!ids[rid]) errors.push({code:'unknown_related_entry_id', path:cp + '.related_entry_ids'}); });
    });
    return {valid: errors.length === 0, errors: errors, totals: {worked:+worked.toFixed(1), billable:+billable.toFixed(1), gap:+(worked-billable).toFixed(1)}};
  }
  function parseImport(raw) {
    if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > MAX_BYTES) throw new Error('Import exceeds the 1 MB limit.');
    var doc = JSON.parse(raw); walk(doc, 0); var result = validateLog(doc);
    if (!result.valid) { var e = new Error('Import failed validation.'); e.details = result.errors; throw e; }
    return doc;
  }
  function previewImport(current, incoming, mode) {
    if (mode !== 'merge' && mode !== 'replace') throw new Error('Choose merge or replace.');
    var checked = validateLog(incoming); if (!checked.valid) throw new Error('Import failed validation.');
    if (mode === 'replace') return {document:clone(incoming), conflicts:[]};
    if (current.learner_id !== incoming.learner_id || current.offering_id !== incoming.offering_id ||
        current.week.start !== incoming.week.start || current.week.end !== incoming.week.end) {
      throw new Error('Merge requires the same learner, offering, and week. Choose replace to use this file instead.');
    }
    var out = clone(current), byId = {}, conflicts = [], contributionById = {};
    out.entries.forEach(function (e) { byId[e.id] = e; });
    incoming.entries.forEach(function (e) {
      if (!byId[e.id]) out.entries.push(clone(e));
      else if (JSON.stringify(byId[e.id]) !== JSON.stringify(e)) {
        conflicts.push({id:e.id, existing:clone(byId[e.id]), incoming:clone(e)});
        out.entries = out.entries.filter(function (x) { return x.id !== e.id; });
      }
    });
    out.contribution_log.forEach(function (c) { contributionById[c.id] = c; });
    incoming.contribution_log.forEach(function (c) {
      if (!contributionById[c.id]) out.contribution_log.push(clone(c));
      else if (JSON.stringify(contributionById[c.id]) !== JSON.stringify(c)) {
        conflicts.push({id:c.id, kind:'contribution', existing:clone(contributionById[c.id]), incoming:clone(c)});
        out.contribution_log = out.contribution_log.filter(function (x) { return x.id !== c.id; });
      }
    });
    var merged = validateLog(out);
    if (!conflicts.length && !merged.valid) throw new Error('Merged import failed validation.');
    return {document:out, conflicts:conflicts};
  }
  function safeCell(v) { var s = String(v == null ? '' : v); return /^[=+\-@]/.test(s) ? "'" + s : s; }
  function quote(v) { var s=safeCell(v); return '"' + s.replace(/"/g, '""') + '"'; }
  function toCSV(doc) {
    var rows=[HEADERS];
    doc.entries.forEach(function (e) { rows.push(['entry',e.id,e.date,e.project,e.matter,e.activity,e.worked_hours,e.billable_hours,+(e.worked_hours-e.billable_hours).toFixed(1),e.class_time,e.narrative,'','','','','','','']); });
    doc.contribution_log.forEach(function (c) { rows.push(['contribution','','','','','','','','','', '',c.id,c.deliverable_id,c.deliverable_title,c.contribution_date,c.contribution_type,c.description,(c.related_entry_ids||[]).join('|')]); });
    return rows.map(function (r) { return r.map(quote).join(','); }).join('\r\n') + '\r\n';
  }
  function parseCSV(text) {
    var rows=[], row=[], cell='', quoted=false;
    for (var i=0;i<text.length;i++) { var ch=text[i]; if (quoted) { if(ch==='"'&&text[i+1]==='"'){cell+='"';i++;} else if(ch==='"')quoted=false; else cell+=ch; } else if(ch==='"')quoted=true; else if(ch===','){row.push(cell);cell='';} else if(ch==='\n'){row.push(cell.replace(/\r$/,''));rows.push(row);row=[];cell='';} else cell+=ch; }
    var headers=rows.shift()||[]; return rows.filter(function(r){return r.length>1;}).map(function(r){var o={};headers.forEach(function(h,j){o[h]=r[j]||'';});return o;});
  }
  function envelope(documents, activeWeekStart) {
    var docs = Array.isArray(documents) ? documents : [documents];
    return {storage_version:2, saved_at:new Date().toISOString(), active_week_start:activeWeekStart || (docs[0] && docs[0].week.start) || '', documents:clone(docs)};
  }
  function readEnvelope(raw) {
    if (typeof raw !== 'string' || raw.length > MAX_BYTES * 5) return {status:'malformed', raw:raw};
    var parsed; try { parsed=JSON.parse(raw); } catch(e) { return {status:'malformed', raw:raw}; }
    if (parsed.storage_version > 2) return {status:'future', raw:raw, version:parsed.storage_version};
    if (parsed.storage_version === 0 && parsed.log) return {status:'ok', migrated_from:0, envelope:envelope(parsed.log, parsed.log.week && parsed.log.week.start)};
    if (parsed.storage_version === 1 && parsed.document) return {status:'ok', migrated_from:1, envelope:envelope(parsed.document, parsed.document.week && parsed.document.week.start)};
    if (parsed.storage_version !== 2 || !Array.isArray(parsed.documents) || parsed.documents.length > MAX_ARRAY || typeof parsed.active_week_start !== 'string') return {status:'malformed', raw:raw};
    var weeks = {}, valid = parsed.documents.every(function (doc) {
      if (!doc || !doc.week || weeks[doc.week.start] || !validateLog(doc).valid) return false;
      weeks[doc.week.start] = true; return true;
    });
    if (!valid || (parsed.documents.length && !weeks[parsed.active_week_start])) return {status:'malformed', raw:raw};
    return {status:'ok', envelope:parsed};
  }
  return {validateLog:validateLog, parseImport:parseImport, previewImport:previewImport, toCSV:toCSV, parseCSV:parseCSV, envelope:envelope, readEnvelope:readEnvelope, limits:{bytes:MAX_BYTES,depth:MAX_DEPTH,string:MAX_STRING,array:MAX_ARRAY}};
}));
