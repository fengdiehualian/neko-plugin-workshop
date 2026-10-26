# From download to your first colony question

New to N.E.K.O? Start here. Learn what each download does, where to install it, and how to connect your colony. This is the separate Guide tab; switch to Panel for settings, connection checks and saved answers. If a step is already complete, jump to the one you need.

## 1. What you need

Install two separate parts: the Bridge Mod goes into RimWorld; the companion plugin goes into N.E.K.O. You need both to ask about your live colony.

- RimWorld 1.6 and Harmony: the game and the shared Mod dependency. In Steam, subscribe to Harmony and wait for its download to finish. For a non-Steam edition, follow the Harmony author’s manual installation instructions.

- N.E.K.O: the desktop companion where you chat and manage plugins. Install it on the same computer as RimWorld.

- Bridge Mod + N.E.K.O RimWorld plugin: the Mod reads the game; the plugin connects to it and prepares advice. Use matching versions; this guide uses Mod v3.2.0.

- A model service: you need its service URL, model name, and an API key if required. The RimWorld consultation model is configured separately from your character’s model.

- [Download N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O/releases)

- [Official N.E.K.O installation instructions](https://github.com/Project-N-E-K-O/N.E.K.O#readme)

- [Get Harmony on Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=2009463077)

- [Download Bridge Mod v3.2.0 (.zip)](https://github.com/wislap/NekoRimWorld/releases/download/v3.2.0/NekoRimWorld-3.2.0.zip)

- [Mod release notes and checksums](https://github.com/wislap/NekoRimWorld/releases/tag/v3.2.0)

- [Get the plugin package (.neko-plugin)](https://github.com/wislap/n.e.k.o_plugin_neko_rimworld/releases)

- [Plugin source repository](https://github.com/wislap/n.e.k.o_plugin_neko_rimworld)

The Mod v3.2.0 ZIP is available now. On the plugin releases page, look for a .neko-plugin attachment under Assets. If none is present, the package is not available yet: wait for it or use the optional source-build instructions below. GitHub’s Source code ZIP is not an installable plugin package.

For a first N.E.K.O installation, choose the official download for your system. On Windows, extract the whole archive, then run N.E.K.O.exe inside it. Complete N.E.K.O’s first-run and chat-model setup, and check that ordinary conversation works before adding this plugin. Use the official instructions above for other systems or source installations.

These instructions cover a local desktop setup. The bridge uses 127.0.0.1 (this computer); remote machines and container networking need a different setup. You do not need bench or a .NET SDK. The plugin reads information and offers advice; it does not issue game orders.

## 2. Install the game Mod

1. Exit RimWorld. Download NekoRimWorld-3.2.0.zip from the link above. Choose this named Mod ZIP rather than Source code.

2. In Steam, right-click RimWorld in your Library and choose Manage → Browse local files to find its installation folder. For another edition, open its installation folder.

3. Extract the ZIP. Put the NekoRimWorld folder inside the game’s Mods folder; create Mods if necessary. Do not put the ZIP itself into Mods.

4. Check the path shown below. About should be directly inside NekoRimWorld; two nested NekoRimWorld folders mean you copied one level too many.

5. Start RimWorld. In Mods, enable Harmony and N.E.K.O RimWorld Bridge. Place Harmony before Core and the Mods that depend on it, then restart when prompted.

```text
RimWorld/Mods/NekoRimWorld/About/About.xml
```

You should now see N.E.K.O RimWorld Bridge in the game’s Mod Settings. The ZIP already includes the game DLL and runtime dependencies; no compilation is needed.

Updating? Exit the game, move the old Mod folder outside the game directory as a backup, then install the new folder. Keep only one enabled copy and use a matching companion plugin version.

## 3. Install the N.E.K.O plugin

Download the .neko-plugin file from the plugin release’s Assets section. Keep it as a single file: Plugin Center will unpack it for you. If this guide is already visible in your installed plugin’s details, you can move on to pairing.

1. Open N.E.K.O’s Plugin Center from the main interface. This is the plugin management page, not RimWorld’s Mods menu.

2. Choose Import and select the downloaded .neko-plugin file. Review the package information and follow the installation prompts.

3. Find N.E.K.O RimWorld in the list, open its details and choose Start. Installing does not start it; auto-start is off by default.

4. In the plugin details, use Guide for these instructions and Panel for the working interface. The panel has four pages: Latest advice, History, Troubleshooting and Settings.

You can return to Guide whenever you need the instructions. If Import is missing or the panel cannot be displayed, check your N.E.K.O version and follow its official update instructions. Importing successfully does not yet verify the game connection or model service.

### Optional: build a plugin package from source

This path is for users comfortable with Git, uv and a terminal. Most players can wait for the .neko-plugin release. Install Git and uv first and follow N.E.K.O’s official source setup requirements. The installed desktop app is not a source checkout.

Use a separate N.E.K.O source checkout with the plugin CLI and Hosted panel support. For a fresh checkout, run the following, then keep your terminal in the resulting N.E.K.O folder:

```text
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

Clone the plugin into plugin/plugins/neko_rimworld, install dependencies and build it. The clone command requires that folder not to exist. For existing sources, use your source-update workflow and check the version against the Mod release before building.

```text
git clone https://github.com/wislap/n.e.k.o_plugin_neko_rimworld.git plugin/plugins/neko_rimworld
uv sync
uv run neko-plugin sync neko_rimworld --clean
uv run neko-plugin build neko_rimworld -o neko_rimworld.neko-plugin
```

The output is neko_rimworld.neko-plugin in the N.E.K.O source folder. Import it into the N.E.K.O installation you actually use, following step 3. Building does not install or start the plugin. An unreleased source build is not a published, tested release.

## 4. Connect your colony

The port identifies the local listener; the pairing code lets the game connect to your plugin. Both ends must use the same values. The default port is 48919.

1. Keep N.E.K.O and its RimWorld plugin running. In RimWorld, open Options → Mod Settings → N.E.K.O RimWorld Bridge.

2. Choose Generate new pairing code, then select Enable N.E.K.O connection. Copy the Pairing code and note the Connection port.

3. Close the game’s settings window to save and apply. Connecting is normal until the plugin has the same details.

4. Switch to Panel → Settings → Game connection, enter the same port and code, then choose Save and apply connection settings.

5. Wait for Saved and applied. Load the colony you want to discuss and leave the game running. Pausing is fine; the main menu alone has no current colony.

6. Switch to Panel → Troubleshooting → Consultation test. In Check the game connection, choose Check the colony overview. This reads the game without calling a model or starting a consultation.

Check that the top status shows Game connected and the current colony, and that the overview matches the save you loaded. This does not test the model service; configure that next.

The pairing-code field stays blank after saving. Leave it blank to keep the stored code. If you generate a new code in the game, update the plugin too. You do not need a new code every time you play.

## 5. Set up the consultation model

RimWorld consultations use their own model service, separate from your character’s chat model and N.E.K.O’s Agent/NekoClaw configuration. Get the URL, exact model ID and key from your provider. An API key is a model-service credential, not the game pairing code.

1. Switch to Panel → Settings → Model service. Enter your provider’s OpenAI-compatible base URL, usually ending in /v1, such as https://your-provider.example/v1. Do not paste the full /chat/completions endpoint.

2. Enter the exact model name supported by that service. It must support streaming Chat Completions and tool calls; the display name may differ from the model ID.

3. Enter the API key if required. Use HTTPS for remote services. Plain HTTP is for a local service, such as http://127.0.0.1:8000/v1; a key cannot be sent to a remote plain-HTTP address.

4. Keep the advanced limits at their defaults for your first attempt. Choose Save model settings and wait for the result before asking a question.

A consultation sends your question and relevant game information to this service and may incur charges. Opening the guide, saving settings, checking the game connection and reading saved history do not call the consultation model.

Configured means the fields are saved, not that the service has passed a live test. Availability is checked on your first consultation. Saved keys are not shown again: leave the field blank to keep the existing key.

If a save is Unconfirmed, check that same save’s status before submitting again. After a plugin restart, review the current URL, model, port and credential-configured indicators. Switching panel pages keeps drafts; closing or reloading the entire panel discards unsaved input.

## 6. Ask your first question

1. Return to your chosen character’s N.E.K.O conversation. Open NekoClaw, the paw/Agent panel (called 猫爪 in Chinese).

2. Turn on the Agent/NekoClaw main switch and User Plugin. If the host asks for Agent model setup or reports the plugin service is unavailable, complete that setup first. Configuring the RimWorld model alone does not enable host tool use.

3. Check that the plugin is still started and your intended colony is loaded. Computer control and browser control are not required for this plugin.

4. Send a clear question mentioning RimWorld and asking to inspect the current game, for example:

> Please use the RimWorld plugin to check my current colony. What should I deal with first, and why?

When a consultation starts, the panel shows progress. You can keep playing or switch panel pages while it runs. Stopping or reloading the plugin interrupts it. After it finishes, read the character’s reply and open Latest advice for the full retained answer.

If conversation does not start a consultation, first check the two switches above. Troubleshooting → Consultation test can test the plugin directly. Run consultation there calls your model and may incur charges; it is different from the game connection check, which does not call a model.

## 7. Read and keep answers

Latest advice shows the latest saved discussion for the current colony. Expand View full answer or copy the full text. Reading a saved answer does not ask the model again.

History lets you search saved questions and characters, or show only the current colony. Check the colony and time on older results. Saved history can be browsed while the game is disconnected.

Continue in the same character’s conversation by mentioning the original question or people involved. A saved answer reflects the game when it was read; it does not update as time passes.

An unavailable export does not always mean the answer is lost. Read or copy retained text before asking again. Remove keys, pairing codes and private information before sharing screenshots or logs.

Next time, start N.E.K.O and this plugin, keep the saved settings, then load your colony. You can enable auto-start in Plugin Center. Before upgrading, stop the plugin and back up settings/history, then import the matching package.

## 8. If a step does not work

Find your symptom below. Opening these explanations does not run tests, change settings or call a model.

### The Mod or plugin does not appear

For a missing Mod, check About/About.xml in step 2 and the Harmony download. For a missing plugin, import the .neko-plugin in N.E.K.O rather than copying it into RimWorld. If the panel is unavailable, check Start and the host version.

### It keeps connecting or rejects the code

Enable N.E.K.O connection in the game and close its settings window to apply. Start the plugin on the same computer with the exact same port and code. A new game code must also be saved in the plugin. If the port is occupied, choose a free port in both places and apply both changes.

### Connected, but there is no current colony

Load a save, wait for its session, then read the colony overview. A main-menu connection is not a loaded colony. After changing saves, refresh and check the colony identity. For a compatibility warning, compare the installed Mod and plugin versions with their release notes.

### Configured, but consultation fails

Read the error, then check the base URL, model ID, key access and provider balance. Streaming Chat Completions and tool calls are required. A game connection check cannot test the model. For overload or timeout, wait before retrying; each consultation may incur charges.

### The character never uses RimWorld

Check the current character’s Agent/NekoClaw main switch and User Plugin, and confirm the plugin is started. Explicitly ask to use the RimWorld plugin. If a direct consultation test works but conversation does not, check the host Agent setup; direct testing alone does not verify conversation routing.

### The save result is unconfirmed

Use Check this save for the existing operation and review current settings. An empty password field does not mean the key or code was erased. Restarting the plugin can remove old receipts, so check applied settings before saving again.

### The answer is incomplete or cannot be downloaded

Read the explanation beside the answer. Incomplete consultation, uncertain history saving and failed export are different outcomes. Copy any retained text and open the answer’s troubleshooting details if needed. Avoid repeating a paid consultation just to obtain a file.

For support, include the N.E.K.O, plugin and Mod versions, the failed step and the visible error. Redact API keys, pairing codes and private information. Never paste credentials into an issue.
