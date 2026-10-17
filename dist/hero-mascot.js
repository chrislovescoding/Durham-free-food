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
    const mobile = matchMedia('(max-width: 700px)').matches;
    const pace = 1.35;
    const sequence = (element, frames, options) => animate(element, frames, {
      ...options, delay: (options.delay || 0) * pace, duration: options.duration * pace,
    });
    let cursor = 220, previous = { x: -width, y: 35 };
    const path = [{ at: 0, transform: moveTo(previous.x, previous.y, .82), opacity: 0 }];
    const addAt = (at, x, y, scale = 1, opacity = 1) => path.push({ at, transform: moveTo(x,y,scale), opacity });
    const add = (time, ...position) => addAt(time * pace, ...position);
    for (const [index, phrase] of phrases.entries()) {
      const box = phrase.before.getBoundingClientRect(), position = point(box, bounds);
      // Mirrored logo: the lower bite faces forward; the upper bite trails.
      const startX = position.x - width * .73, endX = position.x + box.width - width * .73;
      const y = position.y + box.height * .52 - height * .7;
      const arrive = cursor + (index ? 230 : 180), chew = 650, emit = arrive + 200;
      if (index) {
        const hopStart = cursor - 40, takeOff = cursor;
        const hopHeight = Math.max(mobile ? 22 : 26, (y - previous.y) / 4 + 14);
        add(takeOff, previous.x, previous.y);
        for (let step = 1; step <= 14; step++) {
          const t = step / 14;
          add(takeOff + (arrive - takeOff) * t,
            previous.x + (startX - previous.x) * t,
            previous.y + (y - previous.y) * t - 4 * hopHeight * t * (1 - t));
        }
        sequence(body, [
          { transform: 'scale(1,1)', transformOrigin: '50% 100%' },
          { transform: 'scale(1.08,.9)', transformOrigin: '50% 100%', offset: .15 },
          { transform: 'scale(.95,1.06)', transformOrigin: '50% 100%', offset: .4 },
          { transform: 'scale(1.04,.96)', transformOrigin: '50% 100%', offset: .87 },
          { transform: 'scale(1,1)', transformOrigin: '50% 100%' },
        ], { delay: hopStart, duration: arrive - hopStart, fill: 'none', easing: 'ease-in-out' });
      } else add(arrive, startX, y);
      add(arrive + chew, endX, y);
      add(arrive + chew + 210, endX + 12, y - 5, 1.025);
      phrase.inLetters.forEach((letter, i, letters) => {
        const fraction = letters.length === 1 ? 1 : i / (letters.length - 1);
        sequence(letter, [
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
        sequence(letter, [
          { transform: `translate(${dx}px,${dy}px) rotate(-25deg) scale(.08)`, opacity: 0 },
          { transform: `translate(${dx * .78}px,${dy - 23}px) rotate(-12deg) scale(.5)`, opacity: 1, offset: .25 },
          { transform: 'translate(0,-5px) rotate(2deg) scale(1.06)', opacity: 1, offset: .76 },
          { transform: 'translate(0,0) rotate(0) scale(1)', opacity: 1 },
        ], { delay: emit + fraction * 540, duration: 490, easing: 'cubic-bezier(.2,.7,.3,1)' });
      });
      sequence(body, [
        { transform: 'scale(1,1) rotate(0)' },
        { transform: 'scale(1.09,.92) rotate(-5deg)', offset: .2 },
        { transform: 'scale(.94,1.06) rotate(3deg)', offset: .5 },
        { transform: 'scale(1.06,.94) rotate(-3deg)', offset: .75 },
        { transform: 'scale(1,1) rotate(0)' },
      ], { delay: arrive, duration: chew, fill: 'none', iterations: 1 });
      // The lower lobe is the real jaw, split at the neck in the source SVG.
      // Negative rotation lowers its front while the right-hand hinge stays put.
      sequence(jaw, [
        { transform: 'rotate(0deg)' },
        { transform: 'rotate(-14deg)', offset: .42 },
        { transform: 'rotate(-11deg)', offset: .58 },
        { transform: 'rotate(0deg)' },
      ], { delay: arrive, duration: 210, iterations: 3, fill: 'none', easing: 'ease-in-out' });
      crumbs.forEach((crumb, i) => sequence(crumb, [
        { transform: `translate(${endX + width * .73}px,${y + height * .7}px) scale(.2)`, opacity: 0 },
        { transform: `translate(${endX + width * .73 + 11 + i * 6}px,${y + height * .7 - 19 - i * 8}px) scale(1)`, opacity: .85, offset: .35 },
        { transform: `translate(${endX + width * .73 + 25 + i * 5}px,${y + height * .7 + 22}px) scale(0)`, opacity: 0 },
      ], { delay: arrive + chew - 120 + i * 45, duration: 400, fill: 'none' }));
      cursor = arrive + 900; previous = { x: endX + 12, y: y - 5 };
    }
    // All letters have been spat out: crouch and hop while they finish landing.
    // The hop keeps its own brisk timing, independent of the eating pace.
    const jumpStart = (cursor + 20) * pace;
    const takeOff = jumpStart + 140, flight = 620, landing = takeOff + flight;
    const total = landing + 380;
    const arc = Math.min(120, height * .7, Math.min(previous.y, home.y) + 48);
    addAt(jumpStart, previous.x, previous.y);
    addAt(takeOff, previous.x, previous.y);
    for (let step = 1; step <= 24; step++) {
      const t = step / 24;
      addAt(takeOff + flight * t,
        previous.x + (home.x - previous.x) * t,
        previous.y + (home.y - previous.y) * t - 4 * arc * t * (1 - t));
    }
    addAt(landing + 60, home.x, home.y);
    for (let step = 1; step <= 12; step++) {
      const t = step / 12;
      addAt(landing + 60 + 200 * t, home.x, home.y - 4 * 14 * t * (1 - t));
    }
    addAt(total, home.x, home.y);
    const travel = animate(mascot, path.map(({ at, ...frame }) => ({ ...frame, offset: at / total })), { duration: total, easing: 'linear' });
    const spring = [
      [0, 'scale(1,1)'],
      [140, 'scale(1.13,.82)'],
      [195, 'scale(.9,1.13)'],
      [650, 'scale(.97,1.04)'],
      [760, 'scale(1.02,.98)'],
      [805, 'scale(1.24,.74)'],
      [870, 'scale(.95,1.08)'],
      [1020, 'scale(1.06,.95)'],
      [1140, 'scale(1,1)'],
    ];
    animate(body, spring.map(([time, transform]) => ({
      offset: time / 1140, transform, transformOrigin: '50% 100%', easing: 'ease-in-out',
    })), { delay: jumpStart, duration: 1140, fill: 'none' });
    animate(facing, [
      { transform: 'perspective(600px) rotateY(180deg)' },
      { transform: 'perspective(600px) rotateY(180deg)', offset: takeOff / total, easing: 'ease-in-out' },
      { transform: 'perspective(600px) rotateY(-12deg)', offset: (landing - 90) / total },
      { transform: 'perspective(600px) rotateY(0)', offset: landing / total },
      { transform: 'perspective(600px) rotateY(0)' },
    ], { duration: total, easing: 'linear' });
    animate(eye, [{ transform: 'scaleY(1)' },{ transform: 'scaleY(.1)', offset: .45 },{ transform: 'scaleY(1)' }], { delay: total - 250, duration: 200, fill: 'none' });
    travel.finished.then(() => { if (generation === run) finish(); }).catch(() => {});
  } catch { finish(); }
}
replay.addEventListener('click', () => playing ? finish() : play());
reducedMotion.addEventListener('change', finish);
document.addEventListener('visibilitychange', () => { if (document.hidden) { if (playing) finish(); pointer = null; centreEye(); } });
window.addEventListener('resize', () => { if (playing) finish(); else requestGaze(); });
// Wait briefly for type metrics, without making the content depend on fonts.
Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 900))]).then(play);
