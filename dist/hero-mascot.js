const stage = document.querySelector('#munch-stage');
const replay = document.querySelector('#hero-replay');
const mascot = document.querySelector('#mascot-travel');
const facing = mascot?.querySelector('.mascot-facing');
const body = mascot?.querySelector('.mascot-body');
const eye = mascot?.querySelector('.mascot-eye');
const gaze = mascot?.querySelector('.mascot-gaze');
const jaw = mascot?.querySelector('.mascot-jaw');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let animations = [], playing = false, generation = 0;
let pointer = null, gazeFrame = 0, lookX = 0, lookY = 0;

function centreEye() {
  cancelAnimationFrame(gazeFrame); gazeFrame = 0; lookX = 0; lookY = 0;
  gaze.style.setProperty('--look-x', '0px'); gaze.style.setProperty('--look-y', '0px');
}
function updateGaze() {
  gazeFrame = 0;
  if (playing || reducedMotion.matches || document.hidden) { centreEye(); return; }
  const rect = body.getBoundingClientRect();
  let targetX = 0, targetY = 0;
  if (pointer && rect.width && rect.height) {
    const dx = pointer.x - (rect.left + rect.width * .4725);
    const dy = pointer.y - (rect.top + rect.height * .3875);
    const distance = Math.hypot(dx, dy);
    const strength = Math.min(distance / (rect.width * 1.6), 1);
    if (distance) {
      // An elliptical limit keeps the entire pupil inside the logo's white eye.
      targetX = dx / distance * rect.width * .105 * strength;
      targetY = dy / distance * rect.height * .075 * strength;
    }
  }
  lookX += (targetX - lookX) * .24; lookY += (targetY - lookY) * .24;
  const moving = Math.abs(targetX - lookX) + Math.abs(targetY - lookY) > .03;
  if (!moving) { lookX = targetX; lookY = targetY; }
  gaze.style.setProperty('--look-x', `${lookX.toFixed(3)}px`);
  gaze.style.setProperty('--look-y', `${lookY.toFixed(3)}px`);
  if (moving) gazeFrame = requestAnimationFrame(updateGaze);
}
function requestGaze() {
  if (!playing && !reducedMotion.matches && !gazeFrame) gazeFrame = requestAnimationFrame(updateGaze);
}
function releasePointer() { pointer = null; requestGaze(); }
window.addEventListener('pointermove', event => {
  if (event.pointerType !== 'mouse' && event.pointerType !== 'pen') return;
  pointer = { x: event.clientX, y: event.clientY }; requestGaze();
}, { passive: true });
window.addEventListener('pointerout', event => { if (!event.relatedTarget) releasePointer(); });
window.addEventListener('blur', releasePointer);
window.addEventListener('scroll', requestGaze, { passive: true });

// Real text is kept in the h1. These letters are purely decorative, including
// during the transition, so screen readers always get the finished message.
function letterise(node) {
  const letters = Array.from(node.textContent, character => {
    const span = document.createElement('span');
    span.className = 'munch-letter'; span.textContent = character;
    return span;
  });
  node.replaceChildren(...letters);
  return letters;
}
const phrases = [...stage.querySelectorAll('.munch-phrase')].map(phrase => ({
  before: phrase.querySelector('.munch-before'),
  inLetters: letterise(phrase.querySelector('.munch-before')),
  outLetters: [...phrase.querySelectorAll('.munch-after>span')].flatMap(letterise),
}));
const crumbs = [...stage.querySelectorAll('.munch-crumb')];
function animate(element, frames, options) {
  const animation = element.animate(frames, { fill: 'both', ...options });
  animations.push(animation);
  return animation;
}
function finish() {
  generation++; playing = false;
  animations.forEach(animation => animation.cancel()); animations = [];
  stage.classList.remove('is-playing');
  stage.dataset.state = 'settled';
  replay.innerHTML = '<span aria-hidden="true">↻</span> Replay the munch';
  replay.setAttribute('aria-label', 'Replay the mascot animation');
  replay.hidden = reducedMotion.matches;
  if (reducedMotion.matches) centreEye(); else requestGaze();
}
function point(rect, bounds) { return { x: rect.left - bounds.left, y: rect.top - bounds.top }; }
function play() {
  finish();
  if (reducedMotion.matches || !Element.prototype.animate || document.hidden) return;
  const run = generation;
  playing = true; stage.classList.add('is-playing'); stage.dataset.state = 'playing';
  centreEye();
  replay.hidden = false;
  replay.innerHTML = '<span aria-hidden="true">→</span> Skip animation';
  replay.setAttribute('aria-label', 'Skip the mascot animation');
  try {
    const bounds = stage.getBoundingClientRect();
    const rest = mascot.getBoundingClientRect();
    const home = point(rest, bounds), width = rest.width, height = rest.height;
    const moveTo = (x, y, scale = 1) => `translate(${x - home.x}px, ${y - home.y}px) scale(${scale})`;
    const mobile = bounds.width < 660;
    let cursor = 220, previous = { x: -width, y: 35 };
    const path = [{ offset: 0, transform: moveTo(previous.x, previous.y, .82), opacity: 0 }];
    const total = 5350;
    const add = (time, x, y, scale = 1, opacity = 1) => path.push({ offset: time / total, transform: moveTo(x,y,scale), opacity });
    for (const [index, phrase] of phrases.entries()) {
      const box = phrase.before.getBoundingClientRect(), position = point(box, bounds);
      // Mirrored logo: the lower bite faces forward; the upper bite trails.
      const startX = position.x - width * .73, endX = position.x + box.width - width * .73;
      const y = position.y + box.height * .52 - height * .7;
      const arrive = cursor + (index ? 230 : 180), chew = 650, emit = arrive + 200;
      if (index && mobile) {
        add(cursor + 85, previous.x + 12, previous.y + 10, .72);
        add(cursor + 170, startX - 10, y - 15, .72);
      }
      add(arrive, startX, y);
      add(arrive + chew, endX, y);
      add(arrive + chew + 210, endX + 12, y - 5, 1.025);
      phrase.inLetters.forEach((letter, i, letters) => {
        const fraction = letters.length === 1 ? 1 : i / (letters.length - 1);
        animate(letter, [
          { transform: 'translate(0,0) rotate(0) scale(1)', opacity: 1 },
          { transform: 'translate(-3px,-4px) rotate(-8deg) scale(1.08)', opacity: 1, offset: .35 },
          { transform: 'translate(-8px,3px) rotate(12deg) scale(.08)', opacity: 0 },
        ], { delay: arrive + fraction * chew - 90, duration: 180, easing: 'ease-in' });
      });
      phrase.outLetters.forEach((letter, i, letters) => {
        const fraction = letters.length === 1 ? 1 : i / (letters.length - 1);
        const target = letter.getBoundingClientRect(), targetPoint = point(target, bounds);
        const originX = startX + Math.min(1, (200 + fraction * 540) / chew) * (endX - startX) + width * .24;
        const originY = y + height * .18;
        const dx = originX - targetPoint.x - target.width / 2, dy = originY - targetPoint.y - target.height / 2;
        animate(letter, [
          { transform: `translate(${dx}px,${dy}px) rotate(-25deg) scale(.08)`, opacity: 0 },
          { transform: `translate(${dx * .78}px,${dy - 23}px) rotate(-12deg) scale(.5)`, opacity: 1, offset: .25 },
          { transform: 'translate(0,-5px) rotate(2deg) scale(1.06)', opacity: 1, offset: .76 },
          { transform: 'translate(0,0) rotate(0) scale(1)', opacity: 1 },
        ], { delay: emit + fraction * 540, duration: 490, easing: 'cubic-bezier(.2,.7,.3,1)' });
      });
      animate(body, [
        { transform: 'scale(1,1) rotate(0)' },
        { transform: 'scale(1.09,.92) rotate(-5deg)', offset: .2 },
        { transform: 'scale(.94,1.06) rotate(3deg)', offset: .5 },
        { transform: 'scale(1.06,.94) rotate(-3deg)', offset: .75 },
        { transform: 'scale(1,1) rotate(0)' },
      ], { delay: arrive, duration: chew, fill: 'none', iterations: 1 });
      // The lower lobe is the real jaw, split at the neck in the source SVG.
      // Negative rotation lowers its front while the right-hand hinge stays put.
      animate(jaw, [
        { transform: 'rotate(0deg)' },
        { transform: 'rotate(-14deg)', offset: .42 },
        { transform: 'rotate(-11deg)', offset: .58 },
        { transform: 'rotate(0deg)' },
      ], { delay: arrive, duration: 210, iterations: 3, fill: 'none', easing: 'ease-in-out' });
      crumbs.forEach((crumb, i) => animate(crumb, [
        { transform: `translate(${endX + width * .73}px,${y + height * .7}px) scale(.2)`, opacity: 0 },
        { transform: `translate(${endX + width * .73 + 11 + i * 6}px,${y + height * .7 - 19 - i * 8}px) scale(1)`, opacity: .85, offset: .35 },
        { transform: `translate(${endX + width * .73 + 25 + i * 5}px,${y + height * .7 + 22}px) scale(0)`, opacity: 0 },
      ], { delay: arrive + chew - 120 + i * 45, duration: 400, fill: 'none' }));
      cursor = arrive + 1050; previous = { x: endX + 12, y: y - 5 };
    }
    // The last movement is a turn back to the supplied logo's orientation.
    add(cursor + 280, home.x - 5, home.y - 8, 1.035);
    add(total - 180, home.x, home.y + 4, .98);
    add(total, home.x, home.y);
    const travel = animate(mascot, path, { duration: total, easing: 'linear' });
    animate(facing, [
      { transform: 'perspective(600px) rotateY(180deg)' },
      { transform: 'perspective(600px) rotateY(180deg)', offset: (cursor + 40) / total },
      { transform: 'perspective(600px) rotateY(-12deg)', offset: (total - 220) / total },
      { transform: 'perspective(600px) rotateY(0)' },
    ], { duration: total, easing: 'ease-in-out' });
    animate(eye, [{ transform: 'scaleY(1)' },{ transform: 'scaleY(.1)', offset: .45 },{ transform: 'scaleY(1)' }], { delay: total - 340, duration: 230, fill: 'none' });
    travel.finished.then(() => { if (generation === run) finish(); }).catch(() => {});
  } catch { finish(); }
}
replay.addEventListener('click', () => playing ? finish() : play());
reducedMotion.addEventListener('change', finish);
document.addEventListener('visibilitychange', () => { if (document.hidden) { if (playing) finish(); pointer = null; centreEye(); } });
window.addEventListener('resize', () => { if (playing) finish(); else requestGaze(); });
// Wait briefly for type metrics, without making the content depend on fonts.
Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 900))]).then(play);
