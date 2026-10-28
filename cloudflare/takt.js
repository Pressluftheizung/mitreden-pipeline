// Mitreden-Takt (Cloudflare Worker mit Zeitplan "23 * * * *").
//
// Startet jede Stunde den Ablauf "sammeln" bei GitHub und dreimal täglich zusätzlich "kontrolle".
// GitHubs eigener Zeitplan startete bei diesem Repo nicht zuverlässig; ein Worker-Aufruf dauert nur Millisekunden.
//
// Geheimnis (in Cloudflare unter Settings → Variables and Secrets, nie in dieser Datei):
//   GITHUB_TOKEN  Fine-grained Token, nur Repo mitreden-pipeline, nur "Actions: Read and write"

const REPO = "Pressluftheizung/mitreden-pipeline";
const KONTROLLE_STUNDEN_UTC = [6, 13, 18]; // 7, 14, 19 Uhr deutscher Winterzeit, vor den Ausgaben um 8, 15, 20 Uhr

async function starte(ablauf, env) {
  const antwort = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${ablauf}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "mitreden-takt",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (antwort.status !== 204) {
    throw new Error(`${ablauf}: HTTP ${antwort.status} ${await antwort.text()}`);
  }
  console.log(`${ablauf} gestartet`);
}

export default {
  async scheduled(event, env) {
    await starte("sammeln.yml", env);
    // Kontrolle wartet bei GitHub automatisch, bis Sammeln fertig ist (gleiche concurrency-Gruppe).
    if (KONTROLLE_STUNDEN_UTC.includes(new Date(event.scheduledTime).getUTCHours())) {
      await starte("kontrolle.yml", env);
    }
  },

  // Über die Web-Adresse lässt sich nichts auslösen.
  async fetch() {
    return new Response("Mitreden-Takt: läuft nur nach Zeitplan.", { status: 404 });
  },
};
