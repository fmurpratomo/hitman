// A small declarative fetch/swap layer. This is the whole front-end
// framework: no dependencies, no build step, no third-party code.
//
//   data-url      endpoint to call (its presence makes an element a trigger)
//   data-action   HTTP method, default "get"
//   data-target   selector whose innerHTML is replaced with the reply
//   data-form     selector of a form to serialise into the request body
//   data-vals     JSON object of extra body fields
//   data-confirm  text to confirm before firing
//
// A reply may contain <div data-oob="#selector">...</div> elements; their
// contents go to that selector and the rest goes to data-target.

function swap(html, targetSelector) {
  const holder = document.createElement('div');
  holder.innerHTML = html;

  holder.querySelectorAll('[data-oob]').forEach((piece) => {
    const destination = document.querySelector(piece.dataset.oob);
    if (destination) destination.innerHTML = piece.innerHTML;
    piece.remove();
  });

  if (targetSelector) {
    const target = document.querySelector(targetSelector);
    if (target) target.innerHTML = holder.innerHTML;
  }
}

async function fire(trigger) {
  if (trigger.dataset.confirm && !window.confirm(trigger.dataset.confirm)) return;

  const method = (trigger.dataset.action || 'get').toUpperCase();
  const options = { method };

  if (method !== 'GET' && method !== 'DELETE') {
    const form = trigger.dataset.form && document.querySelector(trigger.dataset.form);
    const body = form ? new FormData(form) : new FormData();
    if (trigger.dataset.vals) {
      for (const [key, value] of Object.entries(JSON.parse(trigger.dataset.vals))) {
        body.set(key, value);
      }
    }
    options.body = body;
  }

  trigger.disabled = true;
  try {
    const reply = await fetch(trigger.dataset.url, options);
    const text = await reply.text();
    if (!reply.ok) {
      toast(text);
      return;
    }
    swap(text, trigger.dataset.target);
  } catch (error) {
    toast('Request failed: ' + error.message);
  } finally {
    trigger.disabled = false;
  }
}

// Keep the hidden "_enabled" field in step with its checkbox. An unchecked
// checkbox submits nothing, which would shift the parallel arrays the server
// reads, so the hidden field carries the real value.
document.addEventListener('change', (event) => {
  const box = event.target;
  if (box.classList.contains('toggle')) {
    box.previousElementSibling.value = box.checked ? '1' : '0';
  }
  if (box.id === 'body-type') {
    document.getElementById('body-text').hidden = box.value === 'none' || box.value === 'form';
    document.getElementById('body-form').hidden = box.value !== 'form';
  }
});

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

  if (target.dataset.tab) {
    const bar = target.closest('.tabs');
    bar.querySelectorAll('button').forEach((b) => b.classList.remove('active'));
    target.classList.add('active');
    const scope = bar.parentElement;
    scope.querySelectorAll(':scope > [data-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.panel !== target.dataset.tab;
    });
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

  if (target.classList.contains('add-row')) {
    const table = target.closest('.kv');
    table.querySelector('.rows').appendChild(
      table.querySelector('.row-template').content.cloneNode(true)
    );
    return;
  }

  if (target.classList.contains('remove') && target.closest('.row')) {
    target.closest('.row').remove();
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

let toastTimer;
function toast(message) {
  const element = document.getElementById('toast');
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 2500);
}
