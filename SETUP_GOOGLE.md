# AI Trading Tournament: setup on Google Cloud

## What you end up with

- A small Google Cloud server that runs the engine 24 hours a day, with no browser open.
- A live dashboard at its own web address, with password-protected Start, Pause and Reset buttons.
- Automatic updates: the server checks your GitHub repository every 2 minutes. To change a bot, you upload the new file to GitHub and do nothing else.

## What it costs

- **The server and its disk:** free, under Google's "Free Tier" (one `e2-micro` server in a US region with a 30 GB standard disk).
- **The server's public web address:** about $3.65 a month (roughly £2.80), so around £1.40 for a two-week tournament. Google charges this even on the free server.
- **If you are new to Google Cloud:** you get $300 of free credit for 90 days, which covers that address charge. Google does not bill you when the credit ends unless you choose to upgrade the account.

Google asks for a payment card when you sign up. It is used to check your identity.

## Part 1: Put the files on GitHub (5 minutes)

1. Unzip `tournament_server.zip` on your PC: right-click it → **Extract All**.
2. Copy the other three bots (`gemini_bot.py`, `chatgpt_bot.py`, `grok_bot.py`) into the extracted folder. They must be the versions written from `BOT_PROMPT.md`.
3. Open your `AI-Hedge-Fund` repository on github.com.
4. Click **Add file → Upload files**.
5. Select everything in the extracted folder (**Ctrl+A**) and drag it onto the page.
6. Click the green **Commit changes** button.

**If you uploaded the earlier `tournament.zip`:** the repository now has a `.github` folder that makes GitHub run its own copy of the engine. Switch it off: **Actions** tab → **Tournament engine** → **⋯** (top right) → **Disable workflow**.

## Part 2: Create a Google Cloud account (10 minutes)

1. Go to **console.cloud.google.com** and sign in with your Google account.
2. Accept the terms. If you are offered the free trial, click to start it and fill in the form (country, card details).
3. Look at the top bar, next to the words "Google Cloud". It shows the name of a project, usually **My First Project**. If it says "Select a project", click it, then click **New project**, name it `ai-tournament` and click **Create**. Then click it again and choose that project.

## Part 3: Create the server (5 minutes)

You do this by pasting commands, not by clicking through Google's menus. The menus change often. The commands don't.

1. Click the **Activate Cloud Shell** icon in the top-right corner of the page. It looks like a small box containing `>_`. A black panel opens at the bottom of the page. If asked, click **Continue** or **Authorize**.
2. Wait for a line ending in `$` to appear. Your project's name should be in that line, in yellow.
3. Copy the whole block below, paste it into the black panel (**Ctrl+V**), and press **Enter**.

```
gcloud services enable compute.googleapis.com
gcloud compute instances create tournament --zone=us-east1-b --machine-type=e2-micro --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --boot-disk-type=pd-standard --boot-disk-size=30GB --tags=tournament
gcloud compute firewall-rules create allow-tournament-dashboard --allow=tcp:8501 --target-tags=tournament --source-ranges=0.0.0.0/0
```

It takes about a minute. What each line does:
- Line 1 switches on Google's server feature for your project.
- Line 2 creates the server: the free size (`e2-micro`), in a free US region, with the free type of disk.
- Line 3 opens one door in the server's firewall: port 8501, which the dashboard uses.

**Check:** you should see a small table with the name `tournament` and the status `RUNNING`.

- If it asks a yes/no question, type `y` and press **Enter**.
- If it says the project isn't set, the yellow project name was missing in step 2. Close the panel, pick your project in the top bar, and start Part 3 again.

## Part 4: Install the tournament on the server (10 minutes)

1. Connect to the server. Paste this and press **Enter**:

```
gcloud compute ssh tournament --zone=us-east1-b
```

   The first time, it asks whether to create a key, and then for a "passphrase" twice. Type `y` for the first question, then just press **Enter** twice to leave the passphrase empty.

   **Check:** the line you type on now starts with your name followed by `@tournament`. You are now typing on the server.

2. Paste this and press **Enter**:

```
git clone https://github.com/lloydbennington15-cloud/AI-Hedge-Fund.git ~/tournament && cd ~/tournament && bash server_setup.sh
```

   - It first asks you to choose a **password** for the dashboard's buttons. Use letters and numbers only, at least 6, and nothing you use elsewhere. Nothing shows on screen while you type. That's normal.
   - Then it installs everything. This takes about 5 minutes on this small server.

3. When it finishes it prints a line like this:

```
All done. Open this in your browser:  http://34.123.45.67:8501
```

   That is your dashboard's address. Save it as a bookmark.

You can now close the black panel and the Google page. The tournament keeps running.

## Part 5: Check it and start the tournament

1. Open the dashboard address on your PC or phone. It must start with `http://`, not `https://`.
2. The sidebar should say **🟢 Engine running**. The first results appear after a minute or two.
3. Click each bot's tab under **Bot desks**. A bot with a problem shows a red error box.
4. When all five bots look healthy: type your password in the sidebar, open **⚠️ Reset tournament**, tick the box, and click **Reset everything**. Everyone starts from $100 together.

## Day to day

- **Change a bot:** upload the new file to GitHub (**Add file → Upload files**). The server picks it up within about 2 minutes. The bot keeps its account and positions.
- **Change the engine or dashboard:** the same. The server restarts the right part by itself.
- **Trading hours:** the US market is open 2:30pm–9pm UK time, Monday to Friday. The engine runs all the time, but orders only fill in those hours.
- **Save data allowance:** switch off **Auto-refresh this page** in the sidebar when you aren't watching.

## If something looks wrong

Open Cloud Shell again (the `>_` icon), then paste these two lines one at a time:

```
gcloud compute ssh tournament --zone=us-east1-b
tail -50 ~/tournament/engine.log
```

Send me what it prints.

- **The dashboard address doesn't load:** check it starts with `http://` and ends with `:8501`. If the server was ever stopped and started, its address changes. Get the new one by pasting `gcloud compute instances list` into Cloud Shell and reading the `EXTERNAL_IP` column.
- **A yellow "Yahoo is limiting" banner that doesn't go away:** tell me. The engine can be switched to get prices from Alpaca instead.
- **The dashboard feels very slow:** the free server is the smallest size. Tell me, and I'll give you one command to move it up a size (about $13 a month, covered by the free credit).

## When the tournament is over

Delete the server so the web-address charge stops. Paste this into Cloud Shell and answer `y`:

```
gcloud compute instances delete tournament --zone=us-east1-b
```
