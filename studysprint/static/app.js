/*
 * Study Sprint front end: one page, five screens (start, building, reading, quiz, results)
 * and a movement-break modal.
 *
 * The server owns everything that matters: lesson content, which exercise is assigned,
 * which breaks were completed, the answer key and the score. The browser runs the
 * visible 1-minute countdown and asks the server for a break when it reaches zero.
 *
 * Timer design: the countdown stores a deadline (Date.now() + 60 s) and a 250 ms
 * interval re-renders "time left" from it. Measuring against the clock (instead of
 * counting ticks) keeps it accurate when the tab is throttled, and lets tests
 * fast-forward time with Playwright's clock API.
 *
 * Security: every agent-written string is inserted with textContent (never innerHTML),
 * and the diagram is shown as an <img> data URL, where browsers never run scripts.
 */
"use strict";

const state = {
  config: null,       // /api/config: grades, timer_seconds, exercises, demo
  lesson: null,       // public lesson view (no answer keys)
  sessionId: null,
  phase: "start",     // start | building | reading | quiz | results
  breakId: null,      // pending break, if any
};

const $ = (id) => document.getElementById(id);

/** Call the JSON API. Throws an Error whose message is the server's user-facing text. */
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong. Please try again.");
  return data;
}

/** Show exactly one screen and remember the phase. The timer is visible only while reading or quizzing. */
function show(screen) {
  for (const name of ["start", "building", "reading", "quiz", "results"]) {
    $("screen-" + name).hidden = name !== screen;
  }
  state.phase = screen;
  $("timer").hidden = !(screen === "reading" || screen === "quiz");
  window.scrollTo(0, 0);
}

/* ------------------------------------------------------------------ timer */

const timer = {
  deadline: null,
  interval: null,

  /** Start (or restart) a full countdown. */
  start() {
    this.stop();
    this.deadline = Date.now() + state.config.timer_seconds * 1000;
    this.render();
    this.interval = setInterval(() => this.render(), 250);
  },

  /** Stop counting (during a break, or between phases). */
  stop() {
    if (this.interval) clearInterval(this.interval);
    this.interval = null;
  },

  /** Update the display. When time is up, stop and start a movement break. */
  render() {
    const left = Math.max(0, Math.ceil((this.deadline - Date.now()) / 1000));
    $("timer-value").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    $("timer").classList.toggle("low", left <= 10);
    if (left === 0 && this.interval) {
      this.stop();
      startBreak();
    }
  },
};

/* ------------------------------------------------------------------ breaks */

/** Ask the server for a random exercise and show the modal. The page underneath is hidden. */
async function startBreak() {
  try {
    const b = await api("POST", `/api/sessions/${state.sessionId}/break`);
    state.breakId = b.break_id;
    $("break-exercise").textContent = b.label;
    $("break-modal").hidden = false;
    $("break-done").focus();
  } catch (err) {
    // A failed request mustn't freeze the learner: restart the timer and try again next minute.
    timer.start();
  }
}

/** Record the completed exercise, close the modal and restart the 1-minute timer. */
async function finishBreak() {
  $("break-done").disabled = true;
  try {
    await api("POST", `/api/sessions/${state.sessionId}/break/${state.breakId}/done`);
    state.breakId = null;
    $("break-modal").hidden = true;
    timer.start();
  } finally {
    $("break-done").disabled = false;
  }
}

/* ------------------------------------------------------------------ start + building */

/** Fill the grade dropdown from the server's config, so grades are defined in one place. */
function renderGrades() {
  const select = $("grade");
  select.replaceChildren(new Option("Choose your grade…", ""));
  for (const g of state.config.grades) select.add(new Option(g.label, g.code));
}

/** Validate the start form, request a lesson and poll until it's ready. */
async function onStart(event) {
  event.preventDefault();
  const subject = $("subject").value.trim();
  const grade = $("grade").value;
  const error = $("start-error");
  error.hidden = true;
  if (subject.length < 2 || !grade) {
    error.textContent = "Please type a subject and choose your grade.";
    error.hidden = false;
    return;
  }
  $("start-button").disabled = true;
  try {
    const job = await api("POST", "/api/lessons", { subject, grade });
    if (job.status === "declined") throw new Error(job.message);
    show("building");
    await pollLesson(job.job_id);
  } catch (err) {
    show("start");
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    $("start-button").disabled = false;
  }
}

/** Poll the build every 700 ms, updating the stage list, until it's ready or fails. */
async function pollLesson(jobId) {
  const seen = [];
  for (;;) {
    const s = await api("GET", `/api/lessons/${jobId}`);
    if (s.stage && seen[seen.length - 1] !== s.stage) seen.push(s.stage);
    $("build-stage").textContent = s.stage;
    $("build-steps").replaceChildren(...seen.map((text, i) => {
      const li = document.createElement("li");
      li.textContent = text;
      if (i < seen.length - 1 || s.status === "ready") li.className = "done";
      return li;
    }));
    if (s.status === "ready") {
      state.lesson = s.lesson;
      state.sessionId = s.session_id;
      renderReading();
      return;
    }
    if (s.status === "declined" || s.status === "failed") throw new Error(s.message);
    await new Promise((r) => setTimeout(r, 700));
  }
}

/* ------------------------------------------------------------------ reading */

/** Render the three concepts and the diagram, then start the reading timer. */
function renderReading() {
  const L = state.lesson;
  $("lesson-grade").textContent = `${L.grade_label} · ${L.subject}`;
  $("lesson-topic").textContent = L.topic;
  $("concepts").replaceChildren(...L.concepts.map((c, i) => {
    const card = document.createElement("article");
    card.className = "concept";
    const h = document.createElement("h2");
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = String(i + 1);
    h.append(num, document.createTextNode(c.title));
    const p = document.createElement("p");
    p.textContent = c.explanation;
    const ex = document.createElement("p");
    ex.className = "example";
    ex.textContent = "Example: " + c.example;
    card.append(h, p, ex);
    return card;
  }));
  // A data: URL in an <img> never runs scripts, even if a sanitizer bug let one through.
  $("diagram-img").src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(L.diagram.svg);
  $("diagram-img").alt = L.diagram.alt_text;
  $("diagram-caption").textContent = L.diagram.caption;
  $("finished-reading").checked = false;
  show("reading");
  timer.start();
}

/** "Finished Reading" was selected: tell the server, then open the quiz with a fresh timer. */
async function onFinishedReading() {
  try {
    await api("POST", `/api/sessions/${state.sessionId}/finish-reading`);
    renderQuiz();
  } catch (err) {
    $("finished-reading").checked = false;
  }
}

/* ------------------------------------------------------------------ quiz */

/** Render the five questions as radio groups. Submitting is enabled once all are answered. */
function renderQuiz() {
  $("questions").replaceChildren(...state.lesson.questions.map((q, qi) => {
    const box = document.createElement("div");
    box.className = "question";
    const fs = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = `${qi + 1}. ${q.question}`;
    fs.append(legend);
    q.options.forEach((opt, oi) => {
      const label = document.createElement("label");
      label.className = "option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `q${qi}`;
      input.value = String(oi);
      const span = document.createElement("span");
      span.textContent = opt;
      label.append(input, span);
      fs.append(label);
    });
    box.append(fs);
    return box;
  }));
  $("submit-quiz").disabled = true;
  $("quiz-error").hidden = true;
  show("quiz");
  timer.start();
}

/** The chosen option index for each question, or null where unanswered. */
function quizAnswers() {
  return state.lesson.questions.map((_, qi) => {
    const checked = document.querySelector(`input[name="q${qi}"]:checked`);
    return checked ? Number(checked.value) : null;
  });
}

/** Grade on the server and show the results. */
async function onSubmitQuiz(event) {
  event.preventDefault();
  const answers = quizAnswers();
  if (answers.includes(null)) return;
  $("submit-quiz").disabled = true;
  try {
    const results = await api("POST", `/api/sessions/${state.sessionId}/submit`, { answers });
    timer.stop();
    renderResults(results);
  } catch (err) {
    $("quiz-error").textContent = err.message;
    $("quiz-error").hidden = false;
    $("submit-quiz").disabled = false;
  }
}

/* ------------------------------------------------------------------ results */

/** Show the score, the percentage, the celebration, the exercises completed and the answer review. */
function renderResults(r) {
  $("results-headline").textContent = r.headline;
  $("results-detail").textContent = r.detail;
  $("results-score").textContent = `${r.score} / ${r.total}`;
  $("results-percent").textContent = `${r.percent}%`;
  const list = $("results-exercises");
  if (r.exercises.length === 0) {
    const li = document.createElement("li");
    li.textContent = "No breaks needed. You were speedy!";
    list.replaceChildren(li);
  } else {
    list.replaceChildren(...r.exercises.map((e) => {
      const li = document.createElement("li");
      li.textContent = e.times > 1 ? `${e.label} (${e.times} times)` : e.label;
      return li;
    }));
  }
  $("results-review").replaceChildren(...r.review.map((item) => {
    const li = document.createElement("li");
    const q = document.createElement("div");
    q.textContent = item.question;
    const a = document.createElement("div");
    a.className = item.correct ? "right" : "wrong";
    // Gentle wording for misses: young learners should feel encouraged, not marked down.
    a.textContent = item.correct ? `✓ ${item.your_answer}` : `Not quite: you chose "${item.your_answer}". The answer is "${item.correct_answer}".`;
    const why = document.createElement("div");
    why.className = "why";
    why.textContent = item.why;
    li.append(q, a, why);
    return li;
  }));
  show("results");
}

/** Start over: clear everything and return to the subject/grade form. */
function startOver() {
  timer.stop();
  Object.assign(state, { lesson: null, sessionId: null, breakId: null });
  $("start-form").reset();
  $("break-modal").hidden = true;
  show("start");
  $("subject").focus();
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  state.config = await api("GET", "/api/config");
  $("demo-banner").hidden = !state.config.demo;
  renderGrades();
  $("start-form").addEventListener("submit", onStart);
  $("finished-reading").addEventListener("change", onFinishedReading);
  $("quiz-form").addEventListener("change", () => {
    $("submit-quiz").disabled = quizAnswers().includes(null);
  });
  $("quiz-form").addEventListener("submit", onSubmitQuiz);
  $("break-done").addEventListener("click", finishBreak);
  $("start-over").addEventListener("click", startOver);
  show("start");
}

document.addEventListener("DOMContentLoaded", boot);
