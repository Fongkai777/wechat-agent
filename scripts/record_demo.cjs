// Real browser interactions; no mocked model responses or authored answers.
const {chromium} = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const args = Object.fromEntries(process.argv.slice(2).reduce((rows, arg, i, all) =>
  i % 2 ? rows : [...rows, [arg.replace(/^--/, ''), all[i + 1]]], []));
const output = args.output;
const ffmpeg = process.env.FFMPEG || 'ffmpeg';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROME_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, deviceScaleFactor: 1});
  const page = await context.newPage();
  const scenes = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  async function capture(name, seconds, caption, action = async () => {}) {
    console.log(`Recording: ${name}`);
    const directory = path.join(output, name);
    await fs.mkdir(directory, {recursive: true});
    const started = Date.now();
    let actionError;
    const activity = action().catch(error => {actionError = error;});
    for (let i = 0; i < seconds * 10; i++) {
      if (actionError) throw actionError;
      await page.screenshot({path: path.join(directory, `${String(i).padStart(4, '0')}.jpg`), type: 'jpeg', quality: 85});
      await sleep(Math.max(0, started + (i + 1) * 100 - Date.now()));
    }
    await activity;
    if (actionError) throw actionError;
    scenes.push({name, seconds, caption});
  }
  async function tab(view) { await page.locator(`[data-view="${view}"]`).click(); }
  try {
    await page.goto(args.url, {waitUntil: 'networkidle'});
    await page.locator('#chatList .chat-item').first().waitFor();
    await capture('01-browse', 10, '1 / 6   Browse private + group chats | Fictional data only', async () => {
      await page.locator('#chatList .chat-item').filter({hasText: 'AI 实习交流'}).click();
      await sleep(4000);
      await page.locator('#chatList .chat-item').filter({hasText: '林晓'}).click();
    });
    await tab('qa');
    await capture('02-question', 10, '2 / 6   Ask across conversations | Real model request', async () => {
      await page.locator('#qaQuestion').pressSequentially('星桥实习的要求是什么？林晓建议我怎么准备面试？', {delay: 80});
      await sleep(1500);
      await page.locator('#qaAskBtn').click();
    });
    await page.locator('.qa-answer-paragraph').first().waitFor({timeout: 300000});
    await page.locator('#qaMessages').evaluate(el => {el.scrollTop = 0;});
    await capture('03-evidence', 14, '3 / 6   Expand original evidence | API waiting time removed', async () => {
      await sleep(2000);
      const citations = page.locator('.qa-citation');
      const count = await citations.count();
      for (let i = 0; i < Math.min(count, 2); i++) {
        await citations.nth(i).locator('summary').click();
        await sleep(2500);
      }
      const privateEvidence = page.locator('.qa-citation').filter({hasText: '私聊'}).first();
      if (await privateEvidence.count()) {
        await privateEvidence.scrollIntoViewIfNeeded();
        if (!(await privateEvidence.getAttribute('open'))) await privateEvidence.locator('summary').click();
      }
    });
    await tab('goals');
    await capture('04-create-task', 13, '4 / 6   Create a daily task | Search the last 30 days', async () => {
      await page.locator('#goalNewBtn').click();
      await page.locator('#goalPrompt').pressSequentially('整理星桥搜索实习的申请要求和面试准备建议。', {delay: 65});
      await page.locator('#goalIntervalUnit').selectOption('days');
      await page.locator('#goalRangeValue').fill('30');
      await sleep(1500);
      await page.locator('#goalSaveBtn').click();
      await page.locator('.goal-run-button').waitFor();
      await sleep(1800);
      await page.locator('.goal-run-button').click();
    });
    await page.locator('.goal-output-status[data-state="completed"]').waitFor({timeout: 420000});
    await capture('05-task-result', 11, '5 / 6   Inspect findings + suggested follow-ups | API waiting time removed', async () => {
      await sleep(5000);
      await page.locator('.goal-output-body').evaluate(el => {el.scrollTop = el.scrollHeight;});
    });
    await tab('raw');
    await capture('06-new-messages', 9, '6 / 6   Import 10 new fictional messages | 30 to 40 messages', async () => {
      await sleep(1500);
      const response = await context.request.post(args.url + '/api/demo/append');
      const payload = await response.json();
      if (payload.inserted !== 10) throw new Error('Expected exactly 10 new messages');
      await page.reload({waitUntil: 'networkidle'});
      await page.locator('#chatList .chat-item').filter({hasText: 'AI 实习交流'}).click();
    });
    await tab('rag');
    await page.locator('#ragRebuildBtn').waitFor();
    await capture('07-incremental-index', 10, 'Incremental update | Reuse the existing index; process only new messages', async () => {
      await sleep(1800);
      await page.locator('#ragRebuildBtn').click();
      await page.locator('#ragLog').getByText(/新增\s*10/).first().waitFor({timeout: 30000});
    });
    if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`);
    await page.screenshot({path: path.join(output, 'final.png')});
    await fs.writeFile(path.join(output, 'scenes.json'), JSON.stringify(scenes, null, 2));
    for (const scene of scenes) {
      const caption = scene.caption.replace(/'/g, '').replace(/:/g, '\\:');
      const result = spawnSync(ffmpeg, ['-y', '-loglevel', 'error', '-framerate', '10',
        '-i', path.join(output, scene.name, '%04d.jpg'), '-vf',
        `pad=iw:ih+76:0:0:color=0x202b29,drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:text='${caption}':fontsize=23:fontcolor=white:x=28:y=h-48`,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-pix_fmt', 'yuv420p', path.join(output, scene.name + '.mp4')], {encoding: 'utf8'});
      if (result.status) throw new Error(result.stderr);
    }
    await fs.writeFile(path.join(output, 'clips.txt'), scenes.map(s => `file '${s.name}.mp4'`).join('\n'));
    const result = spawnSync(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
      '-i', path.join(output, 'clips.txt'), '-c', 'copy', '-movflags', '+faststart', path.join(output, 'wechat-agent-demo.mp4')], {encoding: 'utf8'});
    if (result.status) throw new Error(result.stderr);
    console.log('Completed: 77-second real-application demo.');
  } finally {
    await context.close();
    await browser.close();
  }
}
main().catch(error => {console.error(error.message); process.exitCode = 1;});
