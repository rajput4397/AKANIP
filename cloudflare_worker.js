import { parse } from "yaml";

const IST_OFFSET_MS = 330 * 60 * 1000;
const DAILY_CRON = "20 9 * * MON-FRI";
const INTRADAY_CRONS = new Set([
  "*/15 4-9 * * MON-FRI",
]);
const CONFIGS = {
  daily: "config_options_daily.yaml",
  "15min": "config_options_15min.yaml",
};
const TELEGRAM_CHUNK_LIMIT = 3800;

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function istDateString(date = new Date()) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(date)
      .map(({ type, value }) => [type, value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function shiftDate(dateString, days) {
  const date = new Date(`${dateString}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function formatCandleTime(timestamp) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(timestamp))
      .map(({ type, value }) => [type, value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

function parseCsv(text) {
  const rows = [];
  for (const line of text.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    if (!line.trim()) continue;
    const fields = [];
    let field = "";
    let quoted = false;
    for (let index = 0; index < line.length; index += 1) {
      const character = line[index];
      if (character === '"') {
        if (quoted && line[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = !quoted;
        }
      } else if (character === "," && !quoted) {
        fields.push(field);
        field = "";
      } else {
        field += character;
      }
    }
    fields.push(field);
    rows.push(fields);
  }

  const headers = rows.shift()?.map((header) => header.trim()) ?? [];
  return rows.map((fields) =>
    Object.fromEntries(headers.map((header, index) => [header, fields[index]?.trim() ?? ""])),
  );
}

function candlesFromResponse(data) {
  return (data?.data?.candles ?? [])
    .map((row) => ({
      timestamp: Date.parse(row[0]),
      open: Number(row[1]),
      high: Number(row[2]),
      low: Number(row[3]),
      close: Number(row[4]),
      volume: Number(row[5]),
      oi: Number(row[6] ?? 0),
    }))
    .filter((candle) => Number.isFinite(candle.timestamp) && Number.isFinite(candle.close))
    .sort((left, right) => left.timestamp - right.timestamp);
}

async function fetchCandles(url, token) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
        },
      });
      if (response.status === 429 && attempt < 2) {
        await sleep(3000 * (2 ** attempt));
        continue;
      }
      if (!response.ok) {
        console.error(`Upstox returned HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`);
        return null;
      }
      return candlesFromResponse(await response.json());
    } catch (error) {
      if (attempt === 2) {
        console.error(`Upstox request failed: ${error}`);
        return null;
      }
      await sleep(1000);
    }
  }
  return null;
}

async function readAsset(env, path) {
  const response = await env.ASSETS.fetch(`https://assets.local/${path}`);
  if (!response.ok) throw new Error(`Could not read deployment asset: ${path}`);
  return response.text();
}

function resampleTo15Minutes(candles) {
  const buckets = new Map();
  for (const candle of candles) {
    const local = new Date(candle.timestamp + IST_OFFSET_MS);
    const minuteOfDay = local.getUTCHours() * 60 + local.getUTCMinutes();
    const bucketMinute = Math.floor((minuteOfDay - 15) / 15) * 15 + 15;
    const localMidnight = Date.UTC(
      local.getUTCFullYear(),
      local.getUTCMonth(),
      local.getUTCDate(),
    );
    const timestamp = localMidnight + (bucketMinute * 60_000) - IST_OFFSET_MS;
    let bucket = buckets.get(timestamp);
    if (!bucket) {
      bucket = {
        timestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
        oi: candle.oi,
      };
      buckets.set(timestamp, bucket);
    } else {
      bucket.high = Math.max(bucket.high, candle.high);
      bucket.low = Math.min(bucket.low, candle.low);
      bucket.close = candle.close;
      bucket.volume += candle.volume;
      bucket.oi = candle.oi;
    }
  }
  return [...buckets.values()].sort((left, right) => left.timestamp - right.timestamp);
}

function appendTodayDaily(candles, intraday) {
  if (!intraday?.length) return candles;
  const lastIntraday = intraday[intraday.length - 1];
  const local = new Date(lastIntraday.timestamp + IST_OFFSET_MS);
  const timestamp = Date.UTC(
    local.getUTCFullYear(),
    local.getUTCMonth(),
    local.getUTCDate(),
  ) - IST_OFFSET_MS;
  const today = {
    timestamp,
    open: intraday[0].open,
    high: Math.max(...intraday.map((candle) => candle.high)),
    low: Math.min(...intraday.map((candle) => candle.low)),
    close: lastIntraday.close,
    volume: intraday.reduce((sum, candle) => sum + candle.volume, 0),
    oi: lastIntraday.oi,
  };
  const completed = candles.filter((candle) => {
    const candleLocal = new Date(candle.timestamp + IST_OFFSET_MS);
    return candleLocal.getUTCFullYear() !== local.getUTCFullYear()
      || candleLocal.getUTCMonth() !== local.getUTCMonth()
      || candleLocal.getUTCDate() !== local.getUTCDate();
  });
  return [...completed, today].sort((left, right) => left.timestamp - right.timestamp);
}

function checkDynamicPriceVolumeSpike(candles, parameters) {
  const lookback = Number(parameters.lookback ?? 50);
  if (candles.length < lookback + 2) return null;

  const index = candles.length - 1;
  const current = candles[index];
  const previous = candles[index - 1].close;
  if (!Number.isFinite(previous) || previous === 0) return null;
  const historical = candles.slice(Math.max(0, index - lookback), index);
  const moves = [];
  for (let position = 1; position < historical.length; position += 1) {
    const priorClose = historical[position - 1].close;
    if (priorClose !== 0) {
      moves.push(Math.abs((historical[position].close - priorClose) / priorClose));
    }
  }
  if (!moves.length) return null;

  const averageMove = moves.reduce((sum, move) => sum + move, 0) / moves.length;
  const averageVolume = historical.reduce((sum, candle) => sum + candle.volume, 0) / historical.length;
  const currentMove = Math.abs((current.close - previous) / previous);
  if (
    currentMove >= Number(parameters.price_multiplier ?? 2.2) * averageMove
    && current.volume >= Number(parameters.volume_multiplier ?? 2.2) * averageVolume
  ) {
    return current.close;
  }
  return null;
}

function ewm(values, period) {
  const alpha = 2 / (period + 1);
  const result = new Array(values.length);
  result[0] = values[0];
  for (let index = 1; index < values.length; index += 1) {
    result[index] = alpha * values[index] + (1 - alpha) * result[index - 1];
  }
  return result;
}

function checkEma20BearishRejection(candles, parameters) {
  if (candles.length < 100) return null;
  const closes = candles.map((candle) => candle.close);
  const emas = [20, 50, 100].map((period) => ewm(closes, period));
  const index = candles.length - 1;
  const [ema20, ema50, ema100] = emas;
  const slopesNegative = [ema20, ema50, ema100].every((values) => values[index] < values[index - 1]);
  const bearishStack = ema100[index] > ema50[index] && ema50[index] > ema20[index];
  if (!slopesNegative || !bearishStack) return null;

  const latest = candles[index];
  const threshold = Number(parameters.proximity_threshold ?? 0.015);
  const crossedDown = latest.open > ema20[index] && latest.close < ema20[index];
  const bothBelow = latest.open < ema20[index] && latest.close < ema20[index];
  const openNearEma = ema20[index] !== 0
    && Math.abs(ema20[index] - latest.open) / ema20[index] <= threshold;
  const rejectedFromBelow = bothBelow && latest.close < latest.open && openNearEma;
  return crossedDown || rejectedFromBelow ? latest.close : null;
}

function splitTelegramMessage(message) {
  const chunks = [];
  let current = "";
  for (const line of message.split("\n")) {
    const next = current ? `${current}\n${line}` : line;
    if (next.length > TELEGRAM_CHUNK_LIMIT && current) {
      chunks.push(current);
      current = line;
    } else {
      current = next;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

async function sendTelegram(env, message) {
  if (!env.BOT_TOKEN || !env.CHAT_ID) {
    console.warn("Telegram skipped: BOT_TOKEN or CHAT_ID is not configured");
    return;
  }
  const endpoint = `https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`;
  for (const text of splitTelegramMessage(message)) {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: env.CHAT_ID,
        text,
        disable_web_page_preview: true,
      }),
    });
    if (!response.ok) {
      console.error(`Telegram returned HTTP ${response.status}: ${(await response.text()).slice(0, 300)}`);
    }
  }
}

async function runScan(scanType, env) {
  if (!env.UPSTOX_TOKEN) throw new Error("UPSTOX_TOKEN Worker secret is not configured");
  const config = parse(await readAsset(env, CONFIGS[scanType]));
  const scan = config.scans?.find((item) => item.provider === "upstox");
  if (!scan) throw new Error(`No Upstox scan found in ${CONFIGS[scanType]}`);

  const rows = parseCsv(await readAsset(env, scan.csv_path));
  const instrumentColumn = scan.instrument_key_column ?? "instrument_key";
  const stockColumn = scan.stock_column ?? "stock";
  const instruments = [...new Map(
    rows
      .filter((row) => row[instrumentColumn])
      .map((row) => [row[instrumentColumn], row]),
  ).values()];
  const interval = scan.interval ?? scanType;
  const historyDays = Number(scan.history_days);
  const includeToday = scan.include_today_intraday === true;
  const today = istDateString();
  const startDate = shiftDate(today, -historyDays);
  const endDate = shiftDate(today, -1);
  const strategies = (scan.strategies ?? []).map((entry) => ({
    name: typeof entry === "string" ? entry : entry.function,
    parameters: typeof entry === "string" ? {} : (entry.parameters ?? {}),
  }));
  const results = new Map(strategies.map(({ name }) => [name, []]));

  console.log(
    `Starting ${scanType} scan: ${instruments.length} instruments, `
    + `from=${startDate}, to=${endDate}, include_today_intraday=${includeToday}`,
  );

  for (const row of instruments) {
    const instrumentKey = row[instrumentColumn].trim();
    const displayName = row[stockColumn]?.trim() || instrumentKey;
    try {
      const encodedKey = encodeURIComponent(instrumentKey);
      const intervalPath = interval === "15min" ? "1minute" : "day";
      const historicalUrl =
        `https://api.upstox.com/v2/historical-candle/${encodedKey}/${intervalPath}/${endDate}/${startDate}`;
      let candles = await fetchCandles(historicalUrl, env.UPSTOX_TOKEN);

      if (candles) {
        if (includeToday) {
          const intradayUrl =
            `https://api.upstox.com/v2/historical-candle/intraday/${encodedKey}/1minute`;
          const intraday = await fetchCandles(intradayUrl, env.UPSTOX_TOKEN);
          if (interval === "15min" && intraday) {
            candles = [...candles, ...intraday].sort((left, right) => left.timestamp - right.timestamp);
          } else if (interval === "daily") {
            candles = appendTodayDaily(candles, intraday);
          }
        }

        if (interval === "15min") candles = resampleTo15Minutes(candles);
        for (const strategy of strategies) {
          let price = null;
          if (strategy.name === "check_dynamic_price_volume_spike") {
            price = checkDynamicPriceVolumeSpike(candles, strategy.parameters);
          } else if (strategy.name === "check_ema20_bearish_rejection") {
            price = checkEma20BearishRejection(candles, strategy.parameters);
          } else {
            console.warn(`Unsupported Cloudflare strategy: ${strategy.name}`);
          }
          if (price !== null) {
            const candleTime = candles[candles.length - 1].timestamp;
            results.get(strategy.name).push({ displayName, price, candleTime });
            console.log(
              `MATCH ${displayName}: ${strategy.name}; LTP=${price.toFixed(2)}; `
              + `candle=${formatCandleTime(candleTime)}`,
            );
          }
        }
      }
    } catch (error) {
      console.error(`Instrument scan failed for ${displayName}: ${error}`);
    }
    await sleep(150);
  }

  const lines = [`${scan.name ?? scanType} SUMMARY`];
  for (const [name, matches] of results) {
    lines.push("", `STRATEGY: ${name.toUpperCase()}`);
    if (matches.length === 0) {
      lines.push("No stocks matched this criteria.");
    } else {
      for (const match of matches) {
        lines.push(
          `- ${match.displayName} (LTP: ${match.price.toFixed(2)}, `
          + `Candle: ${formatCandleTime(match.candleTime)})`,
        );
      }
    }
  }
  const summary = lines.join("\n");
  console.log(summary);
  await sendTelegram(env, summary);
  return summary;
}

export default {
  async scheduled(controller, env) {
    const scanType = controller.cron === DAILY_CRON
      ? "daily"
      : INTRADAY_CRONS.has(controller.cron)
        ? "15min"
        : null;
    if (!scanType) throw new Error(`Unrecognized scheduled cron: ${controller.cron}`);
    await runScan(scanType, env);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/") {
      return new Response(
        "AKANIP Options Scanner is running. Scheduled scans are triggered by Cloudflare Cron; use POST /run for manual execution.",
        {
          status: 200,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        },
      );
    }
    if (url.pathname !== "/run") return new Response("Not found", { status: 404 });
    if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });

    if (!env.MANUAL_RUN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.MANUAL_RUN_TOKEN}`) {
      return new Response("Unauthorized", { status: 401 });
    }

    try {
      const payload = await request.json();
      if (!Object.hasOwn(CONFIGS, payload.scan)) {
        return Response.json({ error: "scan must be 'daily' or '15min'" }, { status: 400 });
      }
      const summary = await runScan(payload.scan, env);
      return Response.json({ status: "ok", summary });
    } catch (error) {
      console.error(`Manual scan failed: ${error}`);
      return Response.json({ error: String(error) }, { status: 500 });
    }
  },
};