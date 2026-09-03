// A small declarative fetch/swap layer. This is the whole front-end
// framework: no dependencies, no build step, no third-party code.
//
//   data-url      endpoint to call (its presence makes an element a trigger)
//   data-action   HTTP method, default "get"
//   data-target   selector whose innerHTML is replaced with the reply
//   data-form     selector of a form to serialise into the request body
//   data-vals     JSON object of extra body fields
//   data-confirm  text to confirm before firing
//   data-open-dialog / data-close-dialog  a <dialog> to open or close after
//
// A reply may contain <div data-oob="#selector">...</div> elements; their
// contents go to that selector and the rest goes to data-target.

function selectTab(bar, name) {
  bar.querySelectorAll('button[data-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === name);
  });
  bar.parentElement.querySelectorAll(':scope > [data-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.panel !== name;
  });
}

// A fragment always arrives with its default tab selected, so a swap would
// otherwise throw you back to History every time you duplicate, delete or save
// something while working in Saved. Remember the choice and put it back.
function captureTabs(root) {
  const state = {};
  root.querySelectorAll('[data-tabs]').forEach((bar) => {
    const active = bar.querySelector('button.active');
    if (active) state[bar.dataset.tabs] = active.dataset.tab;
  });
  return state;
}

function swapInto(destination, html) {
  const tabs = captureTabs(destination);
  destination.innerHTML = html;
  destination.querySelectorAll('[data-tabs]').forEach((bar) => {
    const wanted = tabs[bar.dataset.tabs];
    if (wanted && bar.querySelector(`button[data-tab="${wanted}"]`)) {
      selectTab(bar, wanted);
    }
  });
}

function swap(html, targetSelector) {
  const holder = document.createElement('div');
  holder.innerHTML = html;

  holder.querySelectorAll('[data-oob]').forEach((piece) => {
    const destination = document.querySelector(piece.dataset.oob);
    if (destination) swapInto(destination, piece.innerHTML);
    piece.remove();
  });

  if (targetSelector) {
    const target = document.querySelector(targetSelector);
    if (target) swapInto(target, holder.innerHTML);
  }
}

async function fire(trigger) {
  if (trigger.dataset.confirm && !window.confirm(trigger.dataset.confirm)) return;

  // Any action can be the one that navigates away, and the debounce timer may
  // still be holding the last few keystrokes. Settle the draft before the DOM
  // is swapped out from under the form it would have been read from.
  await flushDraft();

  const method = (trigger.dataset.action || 'get').toUpperCase();
  const options = { method };

  if (method !== 'GET' && method !== 'DELETE') {
    const form = trigger.dataset.form && document.querySelector(trigger.dataset.form);
    const body = form ? new FormData(form) : new FormData();
    // A control that carries its own name/value — a <select>, say — is its own
    // payload and needs no surrounding form.
    if (!form && trigger.name) body.set(trigger.name, trigger.value);
    if (trigger.dataset.vals) {
      for (const [key, value] of Object.entries(JSON.parse(trigger.dataset.vals))) {
        body.set(key, value);
      }
    }
    options.body = body;
  }

  trigger.disabled = true;
  const stopWaiting = showWaiting(trigger);
  try {
    const reply = await fetch(trigger.dataset.url, options);
    const text = await reply.text();
    if (!reply.ok) {
      toast(text);
      return;
    }
    swap(text, trigger.dataset.target);
    if (trigger.dataset.closeDialog) {
      document.querySelector(trigger.dataset.closeDialog).close();
    }
    if (trigger.dataset.openDialog) {
      document.querySelector(trigger.dataset.openDialog).showModal();
    }
  } catch (error) {
    toast('Request failed: ' + error.message);
  } finally {
    stopWaiting();
    trigger.disabled = false;
  }
}

// A request in flight. `disabled` alone reads as "this button is broken", so
// the trigger grows a spinner and the pane it is about to replace dims —
// stale content must not be mistaken for the new response.
//
// Armed on a delay rather than immediately: this app's whole purpose is
// calling localhost, where a reply often lands in under 20ms, and an
// indicator that appears and vanishes in that time is a strobe rather than
// information. It shows only if the request is still running when the timer
// fires. Returns the function that clears it, so every exit path is covered
// by one `finally`.
const WAIT_DELAY_MS = 120;

function showWaiting(trigger) {
  const target = trigger.dataset.target
    ? document.querySelector(trigger.dataset.target)
    : null;

  const timer = setTimeout(() => {
    trigger.setAttribute('aria-busy', 'true');
    // The class goes on the container, which survives the innerHTML swap.
    if (target) target.classList.add('is-loading');
  }, WAIT_DELAY_MS);

  return () => {
    clearTimeout(timer);
    trigger.removeAttribute('aria-busy');
    if (target) target.classList.remove('is-loading');
  };
}

// Keep the hidden "_enabled" field in step with its checkbox. An unchecked
// checkbox submits nothing, which would shift the parallel arrays the server
// reads, so the hidden field carries the real value.
document.addEventListener('change', (event) => {
  const box = event.target;
  if (box.classList.contains('toggle')) {
    box.previousElementSibling.value = box.checked ? '1' : '0';
  }
  if (box.dataset.url) {
    fire(box);
    return;
  }
  scheduleDraft(box);
  if (box.id === 'body-type') {
    document.getElementById('body-text').hidden = box.value === 'none' || box.value === 'form';
    document.getElementById('body-form').hidden = box.value !== 'form';
  }
});

document.addEventListener('input', (event) => scheduleDraft(event.target));

document.addEventListener('click', (event) => {
  const target = event.target;

  // Checked first: every fetch/swap trigger is identified by data-url, and one
  // delegated listener means swapped-in markup is live immediately.
  const trigger = target.closest('[data-url]');
  if (trigger) {
    event.preventDefault();
    fire(trigger);
    return;
  }

  // A close-only control (Cancel) has no data-url, so it never reaches fire().
  if (target.dataset.closeDialog && !target.dataset.url) {
    document.querySelector(target.dataset.closeDialog).close();
    return;
  }

  if (target.dataset.tab) {
    selectTab(target.closest('.tabs'), target.dataset.tab);
    return;
  }

  if (target.classList.contains('clip-toggle')) {
    // Both halves are already in the DOM, escaped by the template; the toggle
    // only flips which one is visible.
    const clip = target.closest('.clip');
    const showingAll = clip.dataset.open === '1';
    clip.querySelector('.clip-short').hidden = !showingAll;
    clip.querySelector('.clip-full').hidden = showingAll;
    clip.dataset.open = showingAll ? '0' : '1';
    target.textContent = showingAll ? clip.dataset.label : 'show less';
    return;
  }

  if (target.id === 'add-step') {
    addStep();
    return;
  }

  if (target.classList.contains('step-remove')) {
    target.closest('.step').remove();
    return;
  }

  // Moving a step is a DOM move: the server reads step order from the order
  // the rows arrive in, so nothing has to be renumbered.
  if (target.classList.contains('step-up')) {
    const step = target.closest('.step');
    if (step.previousElementSibling) step.parentElement.insertBefore(step, step.previousElementSibling);
    return;
  }

  if (target.classList.contains('step-down')) {
    const step = target.closest('.step');
    if (step.nextElementSibling) step.parentElement.insertBefore(step.nextElementSibling, step);
    return;
  }

  if (target.classList.contains('add-row')) {
    const table = target.closest('.kv');
    table.querySelector('.rows').appendChild(
      table.querySelector('.row-template').content.cloneNode(true)
    );
    return;
  }

  if (target.classList.contains('remove') && target.closest('.row')) {
    // Read the owning form before the row leaves the document.
    const form = target.closest('form');
    target.closest('.row').remove();
    scheduleDraft(form);
    return;
  }

  if (target.id === 'import-curl-open') {
    document.getElementById('import-dialog').showModal();
  }

  if (target.id === 'format-json') {
    const area = document.querySelector('textarea[name="body"]');
    try {
      area.value = JSON.stringify(JSON.parse(area.value), null, 2);
    } catch (error) {
      toast('Not valid JSON: ' + error.message);
    }
  }

  if (target.id === 'copy-curl') copyAsCurl();
  if (target.id === 'import-curl-submit') importCurl();
});

async function copyAsCurl() {
  const body = new FormData(document.getElementById('request-form'));
  const reply = await fetch('/export-curl', { method: 'POST', body });
  const command = await reply.text();
  // navigator.clipboard needs a secure context; http://localhost qualifies.
  await navigator.clipboard.writeText(command);
  toast('curl command copied');
}

async function importCurl() {
  const body = new FormData();
  body.append('text', document.getElementById('curl-text').value);
  const reply = await fetch('/import-curl', { method: 'POST', body });
  const text = await reply.text();
  if (!reply.ok) {
    // Spec: a bad paste must leave the existing form untouched.
    toast(text);
    return;
  }
  swap(text, '#builder');
  document.getElementById('import-dialog').close();
}

// A new step is cloned from a server-rendered template whose every uid is the
// placeholder __UID__ — including the ones inside its own nested row
// templates, which is why the substitution is done on the HTML string rather
// than on the cloned nodes.
function addStep() {
  const source = document.getElementById('step-template');
  const holder = document.getElementById('steps');
  if (!source || !holder) return;

  const uid = 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const scratch = document.createElement('div');
  scratch.innerHTML = source.innerHTML.replace(/__UID__/g, uid);
  while (scratch.firstElementChild) holder.appendChild(scratch.firstElementChild);

  const empty = document.getElementById('steps-empty');
  if (empty) empty.hidden = true;
}

// Editing a saved request is kept without being committed, so switching to
// another endpoint to check something is not the same as throwing your work
// away. The checkpoint only moves when you press Update; this is the other
// half, and it must never get in the way of either.
//
// Debounced rather than sent per keystroke, and flushed by fire() before any
// action that could replace the form.
const DRAFT_DELAY_MS = 600;

let draftTimer = null;
let draftForm = null;

function scheduleDraft(origin) {
  // data-request-id is only on a builder holding a saved request. A brand new
  // request has nothing to be a draft of.
  const form = origin && origin.closest && origin.closest('form[data-request-id]');
  if (!form) return;
  draftForm = form;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(flushDraft, DRAFT_DELAY_MS);
}

async function flushDraft() {
  if (!draftTimer) return;
  clearTimeout(draftTimer);
  draftTimer = null;

  const form = draftForm;
  draftForm = null;
  if (!form || !form.isConnected) return;

  try {
    const reply = await fetch(`/requests/${form.dataset.requestId}/draft`, {
      method: 'PUT',
      body: new FormData(form),
    });
    // Revealed here rather than by a swap: re-rendering the builder mid-edit
    // would move the caret out from under whoever is typing.
    const marker = document.getElementById('draft-state');
    if (marker && reply.ok) marker.hidden = reply.headers.get('X-Draft') !== '1';
  } catch (error) {
    // A lost draft costs a few seconds of typing. Interrupting the person
    // editing costs more, so this stays silent.
  }
}

let toastTimer;
function toast(message) {
  const element = document.getElementById('toast');
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 2500);
}
