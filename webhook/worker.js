/**
 * On-demand trigger for the Navy/ML digest bot.
 *
 * Deploy this as a Cloudflare Worker (free tier), point your Telegram bot's
 * webhook at it, and any time you message the bot "/news" it will kick off
 * the GitHub Actions workflow, which fetches fresh items and sends them to
 * you on Telegram - usually within 20-30 seconds.
 *
 * Commands:
 *   /news, /digest  - everything from the last 5 days, including stories
 *                     already sent in an earlier digest
 *   /new            - only stories you have not been sent yet
 *
 * Required secrets (set in Cloudflare dashboard: Settings -> Variables -> Encrypt):
 *   TELEGRAM_BOT_TOKEN  - from BotFather
 *   TELEGRAM_CHAT_ID    - your chat id
 *   GITHUB_PAT          - fine-grained personal access token with
 *                          "Actions: Read and write" permission on the repo
 *   GITHUB_OWNER        - your GitHub username/org
 *   GITHUB_REPO         - the repo name, e.g. navy-ml-digest-bot
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK");
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      return new Response("bad request", { status: 400 });
    }

    const message = update.message;
    if (!message || !message.text) {
      return new Response("OK");
    }

    const chatId = String(message.chat.id);
    const text = message.text.trim().toLowerCase();

    // Only react to messages from your own chat - ignore everyone else.
    if (chatId !== env.TELEGRAM_CHAT_ID) {
      return new Response("OK");
    }

    const RESEND = ["/news", "/digest", "/start"];
    const NEW_ONLY = ["/new", "/newonly"];

    if (RESEND.includes(text) || NEW_ONLY.includes(text)) {
      const resend = RESEND.includes(text) ? "true" : "false";
      const ghResp = await fetch(
        `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/daily-digest.yml/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${env.GITHUB_PAT}`,
            Accept: "application/vnd.github+json",
            "User-Agent": "navy-ml-digest-bot",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ ref: "main", inputs: { resend } }),
        }
      );

      const ack = ghResp.ok
        ? (resend === "true"
            ? "🔄 Pulling the last 5 days — it'll land here in ~20-30 seconds."
            : "🔄 Fetching new stories only — it'll land here in ~20-30 seconds.")
        : `⚠️ Couldn't trigger the digest (GitHub returned ${ghResp.status}). Check your GITHUB_PAT and repo name.`;

      await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text: ack }),
      });
    }

    return new Response("OK");
  },
};
