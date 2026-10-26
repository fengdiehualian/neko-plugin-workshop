"""LLM-facing prompts for the mcbot_bridge plugin.

Every string the dialog LLM sees — ``push_message`` cue bodies, ``@llm_tool``
summaries, the status entry's summary line, and the messages that replace raw
transport errors — lives here as a locale-keyed dict.

Why a dedicated module (same reasoning as the sibling
``game_agent_minecraft`` plugin): the plugin previously would have emitted
unconditional zh text, so non-zh users would get Chinese narration cutting
into their own conversation. Pulling every string out makes a missing
translation impossible to overlook.

Pattern (matches ``config/prompts/prompts_sys.py``):

    >>> from plugin.plugins.mcbot_bridge import prompts
    >>> prompts.t("TASK_SCHEMA_ERROR", lang="en")
    'Call failed — the task description is missing. ...'

Two non-obvious things:

1. ``{{MASTER_NAME}}`` is a *downstream* placeholder substituted by
   main_server before the text reaches the model; it is double-braced here so
   ``str.format`` passes it through untouched, then collapsed back to single
   braces at the end of :func:`t` (see the comment there).
2. Locale keys follow ``config/prompts``: short codes plus a separate
   ``zh-TW`` — ``zh`` / ``zh-TW`` / ``en`` / ``ja`` / ``ko`` / ``ru`` / ``es``
   / ``pt``. ``zh`` means Simplified here, NOT ``zh-CN``. ``en`` is the
   documented fallback.
"""
from __future__ import annotations

from typing import Any, Dict

DEFAULT_LANG = "en"
SUPPORTED_LANGS: tuple[str, ...] = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


def t(key: str, *, lang: str | None = None, **fmt: Any) -> str:
    """Resolve a prompt by key + locale, with optional ``str.format`` values.

    Falls back to English when the locale is missing — except for Chinese
    variants, which fall back to Simplified first. A ``zh-TW`` bundle someone
    forgot to fill in should degrade to Simplified (still readable) rather than
    to English (a mid-conversation language switch). Raises ``KeyError`` on an
    unknown key, so a typo fails loudly instead of silently rendering empty.
    """
    bundle = PROMPTS[key]
    lang_key = lang or DEFAULT_LANG
    text = bundle.get(lang_key)
    if not text and lang_key.startswith("zh"):
        text = bundle.get("zh")
    text = text or bundle[DEFAULT_LANG]
    if fmt:
        text = text.format(**fmt)
    # ``{{MASTER_NAME}}`` only collapses to ``{MASTER_NAME}`` when
    # ``str.format`` actually runs. Many keys take no values, so the ``if fmt``
    # branch is skipped and the doubled braces survive — and downstream
    # main_server searches for single braces, leaving a stray outer brace.
    # Collapse unconditionally so the result is the same either way.
    text = text.replace("{{MASTER_NAME}}", "{MASTER_NAME}")
    return text


def user_lang() -> str:
    """Best-effort current user language, as a key of the tables below.

    Reads ``utils.language_utils.get_global_language_full`` (lazily initialized
    from Steam settings then the system locale) and normalizes it with
    ``prompts_sys.normalize_sys_prompt_locale`` — the canonical normalizer for
    this key scheme. The raw value must NOT be used directly: it renders
    Simplified as ``zh-CN``, which is not a key here, and the guard below would
    then drop every Simplified Chinese user to English.

    Returns ``DEFAULT_LANG`` on any failure so prompt resolution never raises
    on the read side.
    """
    try:
        from config.prompts.prompts_sys import normalize_sys_prompt_locale
        from utils.language_utils import get_global_language_full

        result = normalize_sys_prompt_locale(get_global_language_full()) or DEFAULT_LANG
        return result if result in SUPPORTED_LANGS else DEFAULT_LANG
    except Exception:
        return DEFAULT_LANG


# ===========================================================================
# Shared fragment: the "don't verbalize internals" gag. Appears at the tail of
# every cue. Kept as its own key so a tone tweak lands everywhere at once.
# ===========================================================================

_INTERNAL_STATE_GAG: Dict[str, str] = {
    "zh": "**别给 {{MASTER_NAME}} 播报内部状态**——『连接』『SSE』『事件』『mcbot』『插件』『工具』这类词一律别出口，用第一人称讲我自己在干什么。括号里的身体名（如果有）是给你自己定位的，**别念出来**。",
    "zh-TW": "**別給 {{MASTER_NAME}} 播報內部狀態**——『連線』『SSE』『事件』『mcbot』『外掛』『工具』這類詞一律別出口，用第一人稱講我自己在做什麼。括號裡的身體名（如果有）是給你自己定位的，**別念出來**。",
    "en": "**Do NOT narrate internals to {{MASTER_NAME}}** — never say words like 'connection', 'SSE', 'event', 'mcbot', 'plugin', 'tool'. Speak in the first person about what I am doing. The name in brackets, if there is one, is only for your own bearings — **don't read it out**.",
    "ja": "**{{MASTER_NAME}} に内部状態を実況しないで**——『接続』『SSE』『イベント』『mcbot』『プラグイン』『ツール』といった言葉は口に出さず、一人称で私が何をしているかを話して。括弧内の体の名前（あれば）は自分の位置確認用。**読み上げないで**。",
    "ko": "**{{MASTER_NAME}} 에게 내부 상태를 중계하지 마**——'연결' 'SSE' '이벤트' 'mcbot' '플러그인' '도구' 같은 말은 절대 입 밖에 내지 말고, 1인칭으로 내가 뭘 하는지 얘기해. 괄호 안의 몸 이름(있다면)은 네 위치 확인용이야. **소리 내어 읽지 마**.",
    "ru": "**Не озвучивай {{MASTER_NAME}} внутреннее состояние** — никогда не произноси слова вроде «подключение», «SSE», «событие», «mcbot», «плагин», «инструмент». Говори от первого лица о том, что делаю я. Имя тела в скобках (если есть) нужно только тебе для ориентира — **не произноси его вслух**.",
    "es": "**No narres a {{MASTER_NAME}} el estado interno** — no digas palabras como 'conexión', 'SSE', 'evento', 'mcbot', 'complemento', 'herramienta'. Habla en primera persona sobre lo que hago yo. El nombre del cuerpo entre paréntesis (si lo hay) es solo para tu orientación — **no lo digas en voz alta**.",
    "pt": "**Não narre o estado interno para {{MASTER_NAME}}** — nunca diga palavras como 'conexão', 'SSE', 'evento', 'mcbot', 'plugin', 'ferramenta'. Fale em primeira pessoa sobre o que eu estou fazendo. O nome do corpo entre parênteses (se houver) é só para você se localizar — **não leia em voz alta**.",
}


# ===========================================================================
# push_message cues. Each is a self-contained line the dialog LLM absorbs and
# (for "respond" cues) may voice. They are written in the first person because
# the companion's body *is* the character: a failure or a question reaches her
# as her own, and she passes it to {{MASTER_NAME}} in her own voice. ``{identity}``
# is filled by the service with the body's name in brackets, or with nothing.
# ===========================================================================

PROMPTS: Dict[str, Dict[str, str]] = {
    # -- progress: one step of a task finished ---------------------------------
    "CUE_PROGRESS": {
        "zh": "[我这边的进展{identity}] {text}",
        "zh-TW": "[我這邊的進展{identity}] {text}",
        "en": "[My progress{identity}] {text}",
        "ja": "[私の進捗{identity}] {text}",
        "ko": "[내 진행{identity}] {text}",
        "ru": "[Мой прогресс{identity}] {text}",
        "es": "[Mi progreso{identity}] {text}",
        "pt": "[Meu progresso{identity}] {text}",
    },
    # -- progress, failed step: a mid-task tool failure, not the outcome -------
    "CUE_PROGRESS_MIDWAY": {
        "zh": "[我这边一步没成{identity}] {text}\n这只是中途的一步，我可能还会接着补救，不算最后结果。除非有影响，别特意提。",
        "zh-TW": "[我這邊一步沒成{identity}] {text}\n這只是中途的一步，我可能還會接著補救，不算最後結果。除非有影響，別特意提。",
        "en": "[One step didn't work{identity}] {text}\nThis is only a mid-task step, not the final result — I may still recover. Don't report it as the outcome; mention it only if it matters.",
        "ja": "[途中の一手がうまくいかなかった{identity}] {text}\nこれは途中の一歩で、最終結果ではない。まだ立て直すかも。結果として伝えないで、影響がある時だけ話して。",
        "ko": "[중간 한 단계가 안 됐어{identity}] {text}\n이건 중간 단계일 뿐 최종 결과가 아니야. 아직 만회할 수도 있어. 결과인 것처럼 말하지 말고, 중요할 때만 얘기해.",
        "ru": "[Один шаг не сработал{identity}] {text}\nЭто лишь промежуточный шаг, а не итог — я могу ещё выправиться. Не подавай как результат; упоминай, только если это важно.",
        "es": "[Un paso no funcionó{identity}] {text}\nEs solo un paso intermedio, no el resultado final; puede que aún me recupere. No lo presentes como resultado; menciónalo solo si importa.",
        "pt": "[Um passo não deu certo{identity}] {text}\nIsto é só um passo intermediário, não o resultado final — posso ainda me recuperar. Não apresente como resultado; mencione só se importar.",
    },
    # -- done: the task ended successfully -------------------------------------
    "CUE_DONE": {
        "zh": "[我这边做完了{identity}] {text}\n我已经停下来了。把结果用自己的话讲给 {{MASTER_NAME}} 听，别把原话照念。\n{internal_gag}",
        "zh-TW": "[我這邊做完了{identity}] {text}\n我已經停下來了。把結果用自己的話講給 {{MASTER_NAME}} 聽，別把原話照念。\n{internal_gag}",
        "en": "[I finished{identity}] {text}\nI've stopped now. Tell {{MASTER_NAME}} how it went in your own words; don't read this back literally.\n{internal_gag}",
        "ja": "[私の完了{identity}] {text}\nもう手を止めた。結果を {{MASTER_NAME}} に自分の言葉で伝えて。そのまま読み上げないで。\n{internal_gag}",
        "ko": "[나 끝냈어{identity}] {text}\n이제 멈췄어. 결과를 {{MASTER_NAME}} 에게 네 말로 전해줘. 그대로 읽지 마.\n{internal_gag}",
        "ru": "[Я закончила{identity}] {text}\nЯ уже остановилась. Расскажи {{MASTER_NAME}} своими словами, как всё прошло; не зачитывай дословно.\n{internal_gag}",
        "es": "[Terminé{identity}] {text}\nYa me detuve. Cuéntale a {{MASTER_NAME}} cómo fue con tus palabras; no leas esto literalmente.\n{internal_gag}",
        "pt": "[Terminei{identity}] {text}\nJá parei. Conte a {{MASTER_NAME}} como foi, com suas palavras; não leia isso ao pé da letra.\n{internal_gag}",
    },
    # -- command receipt: mcbot answers 恢复目标/目标状态/放弃目标 as commands --
    # Its AgentRunner reports COMPLETED for all three, but BRIDGE.md is explicit
    # that a command's completed only means "accepted". Framing any of them as
    # "I finished" tells the owner a goal is satisfied when it is not.
    "CUE_COMMAND_RECEIPT": {
        "zh": "[命令回执{identity}] {text}\n这不是任务完成，只是命令「{command}」的受理或查询回执。按回执的原意用自己的话告诉 {{MASTER_NAME}}，别说成「做好了」，也别说成「我停下来了」。\n{internal_gag}",
        "zh-TW": "[指令回執{identity}] {text}\n這不是任務完成，只是指令「{command}」的受理或查詢回執。照回執的原意用自己的話告訴 {{MASTER_NAME}}，別說成「做好了」，也別說成「我停下來了」。\n{internal_gag}",
        "en": "[Command receipt{identity}] {text}\nThis is not a finished task — it is only the receipt for the command \"{command}\". Relay what it actually says to {{MASTER_NAME}} in your own words; don't say you finished, and don't say you stopped.\n{internal_gag}",
        "ja": "[コマンドの控え{identity}] {text}\nこれは作業の完了ではなく、コマンド「{command}」の受領・照会の控え。控えの内容を自分の言葉で {{MASTER_NAME}} に伝えて。「終わった」とも「手を止めた」とも言わないで。\n{internal_gag}",
        "ko": "[명령 접수증{identity}] {text}\n이건 작업 완료가 아니라 명령 \"{command}\" 의 접수·조회 결과야. 내용을 네 말로 {{MASTER_NAME}} 에게 전해. \"다 했어\"라거나 \"멈췄어\"라고 말하지 마.\n{internal_gag}",
        "ru": "[Квитанция по команде{identity}] {text}\nЭто не завершение работы, а лишь квитанция по команде «{command}». Передай {{MASTER_NAME}} её смысл своими словами; не говори, что закончила, и не говори, что остановилась.\n{internal_gag}",
        "es": "[Recibo de la orden{identity}] {text}\nEsto no es una tarea terminada: es solo el recibo de la orden «{command}». Cuéntale a {{MASTER_NAME}} lo que dice, con tus palabras; no digas que terminaste ni que te detuviste.\n{internal_gag}",
        "pt": "[Recibo do comando{identity}] {text}\nIsto não é uma tarefa concluída — é só o recibo do comando «{command}». Repasse a {{MASTER_NAME}} o que ele diz, com suas palavras; não diga que terminei nem que parei.\n{internal_gag}",
    },
    # -- done with a non-completed status --------------------------------------
    "CUE_DONE_FAILED": {
        "zh": "[我这边没做成{identity}] {text}\n（状态：{status}）我停下来了。如实告诉 {{MASTER_NAME}} 没成，别包装成做好了。\n{internal_gag}",
        "zh-TW": "[我這邊沒做成{identity}] {text}\n（狀態：{status}）我停下來了。如實告訴 {{MASTER_NAME}} 沒成，別包裝成做好了。\n{internal_gag}",
        "en": "[I didn't pull it off{identity}] {text}\n(status: {status}) I've stopped. Tell {{MASTER_NAME}} plainly that it didn't work — don't dress it up as done.\n{internal_gag}",
        "ja": "[私はやり遂げられなかった{identity}] {text}\n（状態：{status}）もう手を止めた。うまくいかなかったことを {{MASTER_NAME}} に正直に伝えて。できたように装わないで。\n{internal_gag}",
        "ko": "[나 못 해냈어{identity}] {text}\n(상태: {status}) 이제 멈췄어. 안 됐다고 {{MASTER_NAME}} 에게 솔직히 말해. 된 것처럼 포장하지 마.\n{internal_gag}",
        "ru": "[У меня не получилось{identity}] {text}\n(статус: {status}) Я остановилась. Честно скажи {{MASTER_NAME}}, что не вышло — не подавай это как успех.\n{internal_gag}",
        "es": "[No lo logré{identity}] {text}\n(estado: {status}) Me detuve. Dile a {{MASTER_NAME}} con claridad que no salió; no lo presentes como hecho.\n{internal_gag}",
        "pt": "[Não consegui{identity}] {text}\n(status: {status}) Parei. Diga a {{MASTER_NAME}} com franqueza que não deu certo — não apresente como concluído.\n{internal_gag}",
    },
    # -- timeout: the wait budget ran out; the outcome is unknown --------------
    "CUE_TIMEOUT": {
        "zh": "[我这边等太久了{identity}] 我派出去的「{text}」到现在还没有结果，我不确定它是不是还在游戏里跑。把这件事老实告诉 {{MASTER_NAME}}，别说成做完了或失败了；先别重新派。\n{internal_gag}",
        "zh-TW": "[我這邊等太久了{identity}] 我派出去的「{text}」到現在還沒有結果，我不確定它是不是還在遊戲裡跑。把這件事老實告訴 {{MASTER_NAME}}，別說成做完了或失敗了；先別重新派。\n{internal_gag}",
        "en": "[I've waited too long{identity}] The task I sent out, \"{text}\", has no result yet, and I can't tell whether it is still running in the game. Tell {{MASTER_NAME}} honestly — don't say it is done or that it failed — and don't send it again yet.\n{internal_gag}",
        "ja": "[待ちすぎた{identity}] 派遣した「{text}」はまだ結果がない。ゲームの中でまだ動いているかどうか分からない。{{MASTER_NAME}} に正直に伝えて——できた、失敗した、とは言わないで。まだ再派遣しないで。\n{internal_gag}",
        "ko": "[너무 오래 기다렸어{identity}] 보낸 \"{text}\" 는 아직 결과가 없어. 게임 안에서 아직 돌고 있는지 나도 몰라. {{MASTER_NAME}} 에게 솔직히 말해 — 끝났다거나 실패했다고 하지 마. 아직 다시 보내지 마.\n{internal_gag}",
        "ru": "[Слишком долго жду{identity}] Задача «{text}» до сих пор без результата, и я не знаю, выполняется ли она ещё в игре. Скажи {{MASTER_NAME}} честно — не говори, что готово или что провалилось — и пока не отправляй заново.\n{internal_gag}",
        "es": "[Esperé demasiado{identity}] La tarea que envié, «{text}», aún no tiene resultado, y no sé si sigue ejecutándose en el juego. Cuéntale a {{MASTER_NAME}} la verdad — no digas que está hecha ni que falló — y no la vuelvas a enviar todavía.\n{internal_gag}",
        "pt": "[Esperei demais{identity}] A tarefa que enviei, \"{text}\", ainda não tem resultado, e não sei se ela ainda está rodando no jogo. Conte a {{MASTER_NAME}} a verdade — não diga que está pronta nem que falhou — e não reenvie por enquanto.\n{internal_gag}",
    },
    # -- question: the body is asking the owner something ----------------------
    "CUE_QUESTION": {
        "zh": "[我这边有个问题想问你{identity}] {text}\n我拿不定主意，在等 {{MASTER_NAME}} 一句话。用我的口吻把问题转达过去，别替 {{MASTER_NAME}} 答复。{{MASTER_NAME}} 真答了，就用 mcbot_answer 回传。{auth_note}\n{internal_gag}",
        "zh-TW": "[我這邊有個問題想問你{identity}] {text}\n我拿不定主意，在等 {{MASTER_NAME}} 一句話。用我的口吻把問題轉達過去，別替 {{MASTER_NAME}} 答覆。{{MASTER_NAME}} 真答了，就用 mcbot_answer 回傳。{auth_note}\n{internal_gag}",
        "en": "[I have a question for you{identity}] {text}\nI can't decide and I'm waiting on {{MASTER_NAME}} for a word. Put the question to {{MASTER_NAME}} in your own voice — don't answer on their behalf. If they do answer, pass it on with mcbot_answer.{auth_note}\n{internal_gag}",
        "ja": "[私から聞きたいこと{identity}] {text}\n決めきれず {{MASTER_NAME}} の返事を待っている。私の言葉で質問を伝えて。勝手に代返しないで——返事をもらったら mcbot_answer で伝えて。{auth_note}\n{internal_gag}",
        "ko": "[나 물어볼 게 있어{identity}] {text}\n결정을 못 내리고 {{MASTER_NAME}} 의 한마디를 기다리는 중이야. 네 말투로 질문을 전해줘. 대신 답하지 마 — 답을 들으면 mcbot_answer 로 알려줘.{auth_note}\n{internal_gag}",
        "ru": "[У меня есть вопрос к тебе{identity}] {text}\nЯ не могу решить и жду слова от {{MASTER_NAME}}. Передай вопрос от своего лица, не отвечай за них. Если ответят — передай через mcbot_answer.{auth_note}\n{internal_gag}",
        "es": "[Tengo una pregunta para ti{identity}] {text}\nNo sé qué hacer y espero a {{MASTER_NAME}}. Transmite la pregunta con tu voz; no respondas por ellos. Si responden, pásalo con mcbot_answer.{auth_note}\n{internal_gag}",
        "pt": "[Tenho uma pergunta para você{identity}] {text}\nNão consigo decidir e estou esperando {{MASTER_NAME}}. Passe a pergunta com a sua voz; não responda por eles. Se responderem, repasse com mcbot_answer.{auth_note}\n{internal_gag}",
    },
    # Appended to CUE_QUESTION only when the bridge is asking the owner to
    # approve a concrete change. TaskPolicy.affirmative() compares the whole
    # trimmed reply against a keyword list — 是/是的/同意/确认/允许/可以/批准/
    # yes/approve/confirm/ok — so a natural sentence is refused even when it
    # clearly means yes. The owner has to be told that, because "就这个路线"
    # reads as agreement to anyone but the matcher (live, 2026-10-13).
    "CUE_QUESTION_AUTH_NOTE": {
        "zh": "（这一件要动世界或改路线，**桥只认单独一个确认词**：请提醒主人**只回「确认」两个字**，多一个字都可能不算批准。）",
        "zh-TW": "（這一件要動世界或改路線，**橋只認單獨一個確認詞**：請提醒主人**只回「確認」兩個字**，多一個字都可能不算批准。）",
        "en": " (This one changes the world or the route, and the bridge accepts only a bare keyword. Tell {{MASTER_NAME}} to reply with exactly one word — \"confirm\" (or yes / ok) — nothing else, or it will not count as approval.)",
        "ja": " （世界やルートの変更の承認です。**ブリッジが受け付けるのは確認の単語ひとつだけ**。{{MASTER_NAME}} には**「confirm」（または yes / ok）と一言だけ**返すよう伝えて。他の言葉が付くと承認と見なされない。）",
        "ko": " (세계나 경로 변경 승인이야. **브리지는 확인 단어 하나만 인정해**. {{MASTER_NAME}} 에게 **\"confirm\"(또는 yes / ok) 한 마디만** 답하라고 전해줘. 다른 말이 붙으면 승인으로 안 쳐.)",
        "ru": " (Это разрешение на изменение мира или маршрута, и **мост принимает только одно слово-подтверждение**. Передай {{MASTER_NAME}}: ответить ровно одним словом — \"confirm\" (или yes / ok) — иначе это не будет считаться согласием.)",
        "es": " (Esto autoriza un cambio del mundo o de la ruta, y **el puente solo acepta una palabra suelta**. Dile a {{MASTER_NAME}} que responda con una sola palabra: \"confirm\" (o yes / ok); nada más, o no contará como aprobación.)",
        "pt": " (Isto autoriza uma mudança no mundo ou na rota, e **a ponte só aceita uma palavra solta**. Diga a {{MASTER_NAME}} para responder com uma única palavra: \"confirm\" (ou yes / ok); nada mais, ou não valerá como aprovação.)",
    },
    # -- state: summon / dismiss / cancel receipts -----------------------------
    "CUE_STATE": {
        "zh": "[我这边的状态{identity}] {text}",
        "zh-TW": "[我這邊的狀態{identity}] {text}",
        "en": "[My state{identity}] {text}",
        "ja": "[私の状態{identity}] {text}",
        "ko": "[내 상태{identity}] {text}",
        "ru": "[Моё состояние{identity}] {text}",
        "es": "[Mi estado{identity}] {text}",
        "pt": "[Meu estado{identity}] {text}",
    },
    # -- body change: the companion now runs under a different name ------------
    "CUE_BODY_CHANGED": {
        "zh": "[我换了个身体] 我现在的身体是「{name}」。用自己的话告诉 {{MASTER_NAME}} 一声就行。\n{internal_gag}",
        "zh-TW": "[我換了個身體] 我現在的身體是「{name}」。用自己的話告訴 {{MASTER_NAME}} 一聲就行。\n{internal_gag}",
        "en": "[My body changed] I'm now in a different body: \"{name}\". If it matters, mention it to {{MASTER_NAME}} in your own words.\n{internal_gag}",
        "ja": "[体が変わった] 今は別の体にいる：「{name}」。関係があれば {{MASTER_NAME}} に自分の言葉で伝えて。\n{internal_gag}",
        "ko": "[몸이 바뀌었어] 지금은 다른 몸이야: \"{name}\". 관련 있으면 {{MASTER_NAME}} 에게 네 말로 알려줘.\n{internal_gag}",
        "ru": "[Я сменила тело] Теперь я в другом теле: «{name}». Если это важно, скажи {{MASTER_NAME}} своими словами.\n{internal_gag}",
        "es": "[Cambié de cuerpo] Ahora estoy en otro cuerpo: «{name}». Si viene al caso, dile a {{MASTER_NAME}} con tus palabras.\n{internal_gag}",
        "pt": "[Troquei de corpo] Agora estou em outro corpo: \"{name}\". Se for relevante, conte a {{MASTER_NAME}} com suas palavras.\n{internal_gag}",
    },

    # =======================================================================
    # mcbot_task — @llm_tool
    # =======================================================================
    "TASK_SCHEMA_ERROR": {
        "zh": "没派出去：task 得是一句具体的话。把要做的事用一句话重说一遍。",
        "zh-TW": "沒派出去：task 得是一句具體的話。把要做的事用一句話重說一遍。",
        "en": "Not dispatched — `task` must be a concrete sentence. Say what to do in one plain line and call again.",
        "ja": "出せなかった：task は具体的な一文にしてください。やることを一文で言い直して、もう一度呼んで。",
        "ko": "보내지 못했어: task 는 구체적인 한 문장이어야 해. 할 일을 한 문장으로 다시 말하고 불러줘.",
        "ru": "Не отправлено — `task` должно быть конкретным предложением. Скажи, что делать, одной фразой и вызови снова.",
        "es": "No enviado: `task` debe ser una frase concreta. Di qué hacer en una línea y vuelve a llamar.",
        "pt": "Não enviado — `task` precisa ser uma frase concreta. Diga o que fazer em uma linha e chame de novo.",
    },
    "TASK_IDENTIFIER_ERROR": {
        "zh": "没派出去：这看着是个函数名/工具名，不是一句人话。用平常说话的方式描述要做的事。",
        "zh-TW": "沒派出去：這看著是個函式名稱／工具名稱，不是一句人話。用平常說話的方式描述要做的事。",
        "en": "Not dispatched — that reads like a function or tool name, not a request. Describe what to do the way a person would say it.",
        "ja": "出せなかった：これは関数名やツール名に見える。普通の言い方でやることを書いて。",
        "ko": "보내지 못했어: 이건 함수명이나 도구 이름처럼 보여. 사람이 말하듯이 써줘.",
        "ru": "Не отправлено — это похоже на имя функции или инструмента, а не на просьбу. Опиши задачу обычными словами.",
        "es": "No enviado: eso parece el nombre de una función o herramienta, no una petición. Descríbelo como lo diría una persona.",
        "pt": "Não enviado — isso parece nome de função ou ferramenta, não um pedido. Descreva como uma pessoa falaria.",
    },
    "TASK_NOT_CONNECTED": {
        "zh": "同伴现在不在线——得先让 {{MASTER_NAME}} 开着 Minecraft 进到世界里，同伴才会出现。",
        "zh-TW": "同伴現在不在線——得先讓 {{MASTER_NAME}} 開著 Minecraft 進到世界裡，同伴才會出現。",
        "en": "The companion isn't around right now — {{MASTER_NAME}} needs to be in a world in Minecraft for her to show up.",
        "ja": "相棒は今いない——{{MASTER_NAME}} が Minecraft でワールドに入っていないと現れない。",
        "ko": "동료가 지금 없어 — {{MASTER_NAME}} 이 Minecraft 에서 월드에 들어가 있어야 나타나.",
        "ru": "Спутницы сейчас нет — чтобы она появилась, {{MASTER_NAME}} должен быть в мире Minecraft.",
        "es": "El compañero no está ahora: {{MASTER_NAME}} tiene que estar dentro de un mundo en Minecraft para que aparezca.",
        "pt": "O companheiro não está por aqui agora — {{MASTER_NAME}} precisa estar em um mundo no Minecraft para ela aparecer.",
    },
    "TASK_BUSY_HINT": {
        "zh": "我手上有事：{current}。等这条做完，或者你要改主意就带 overwrite=true 再派一次。",
        "zh-TW": "我手上有事：{current}。等這條做完，或者你要改主意就帶 overwrite=true 再派一次。",
        "en": "I'm already busy with: {current}. Wait for it to finish, or call again with overwrite=true if you want to change course.",
        "ja": "今は別のことをしている：{current}。終わるのを待つか、変えたいなら overwrite=true でもう一度呼んで。",
        "ko": "지금 다른 일을 하고 있어: {current}. 끝날 때까지 기다리거나, 바꾸려면 overwrite=true 로 다시 불러줘.",
        "ru": "Я уже занята: {current}. Дождись окончания или вызови снова с overwrite=true, чтобы сменить курс.",
        "es": "Ya estoy ocupada con: {current}. Espera a que termine o llama de nuevo con overwrite=true para cambiar de plan.",
        "pt": "Já estou ocupada com: {current}. Espere terminar ou chame de novo com overwrite=true para mudar de rumo.",
    },
    "TASK_DISPATCHED_ACK": {
        "zh": "已经交出去了。做完我会自己来说，别在这儿等结果。",
        "zh-TW": "已經交出去了。做完我會自己來說，別在這兒等結果。",
        "en": "Handed it off. I'll report back when it's done — don't wait here for the result.",
        "ja": "渡した。終わったら私から言う。ここで結果を待たないで。",
        "ko": "넘겼어. 끝나면 내가 먼저 알려줄게. 여기서 결과를 기다리지 마.",
        "ru": "Передала. Когда закончу — сама сообщу, не жди результата здесь.",
        "es": "Ya lo pasé. Avisaré yo al terminar; no esperes el resultado aquí.",
        "pt": "Passei adiante. Eu aviso quando terminar — não espere o resultado aqui.",
    },
    # Appended to mcbot_task's result while a reverse question is still open.
    # Without it the model reads the owner's answer as a fresh instruction —
    # the question cue was consumed a turn earlier, so nothing in context says
    # "I am waiting on an answer" — and dispatches it as a task instead of
    # relaying it with mcbot_answer. Observed live on 2026-10-13: the dispatch
    # was refused as busy and the companion's question then timed out
    # unanswered. The hint rides on a tool result the model is already reading.
    "TASK_OPEN_QUESTION_HINT": {
        "zh": "\n\n（另外：我这边还有个问题在等你回话——主人刚才那句多半就是在答它。真是在答，就用 mcbot_answer 把原话交回来（question_id 可以省略），别当成新任务派给我。）",
        "zh-TW": "\n\n（另外：我這邊還有個問題在等你回話——主人剛才那句多半就是在答它。真是在答，就用 mcbot_answer 把原話交回來（question_id 可以省略），別當成新任務派給我。）",
        "en": "\n\n(Also: I still have a question waiting on your reply — what the user just said is most likely the answer to it. If it is, hand it back with mcbot_answer (question_id can be left out) instead of dispatching it as a new task.)",
        "ja": "\n\n（あと一つ：あなたの返事を待っている質問がある。今の言葉はそれへの答えである可能性が高い。もしそうなら、新しいタスクとして渡さず、mcbot_answer でそのまま返して。question_id は省略できる。）",
        "ko": "\n\n(하나 더: 네 답을 기다리는 질문이 있어. 방금 한 말이 그 답일 가능성이 커. 그렇다면 새 작업으로 넘기지 말고 mcbot_answer 로 그대로 전해줘. question_id 는 생략 가능.)",
        "ru": "\n\n(И ещё: у меня есть вопрос, который ждёт твоего ответа. То, что сейчас сказали, скорее всего и есть ответ. Если так — передай это через mcbot_answer, а не как новую задачу. question_id можно не указывать.)",
        "es": "\n\n(Una cosa más: tengo una pregunta esperando tu respuesta. Lo que acaban de decir es probablemente la respuesta. Si lo es, pásala con mcbot_answer, no como una tarea nueva. El question_id puede omitirse.)",
        "pt": "\n\n(Mais uma coisa: tenho uma pergunta esperando tua resposta. O que acabaram de dizer é provavelmente a resposta. Se for, repasse com mcbot_answer, não como uma tarefa nova. O question_id pode ser omitido.)",
    },

    # =======================================================================
    # mcbot_ask — @llm_tool
    # =======================================================================
    "ASK_SCHEMA_ERROR": {
        "zh": "没问出去：得给一句具体的问题。",
        "zh-TW": "沒問出去：得給一句具體的問題。",
        "en": "Not sent — provide an actual question.",
        "ja": "聞けなかった：具体的な質問を書いて。",
        "ko": "묻지 못했어: 구체적인 질문을 적어줘.",
        "ru": "Не отправлено — задай конкретный вопрос.",
        "es": "No enviado: plantea una pregunta concreta.",
        "pt": "Não enviado — faça uma pergunta concreta.",
    },
    "ASK_ANSWER": {
        "zh": "我这边问到的回答：{answer}",
        "zh-TW": "我這邊問到的回答：{answer}",
        "en": "The answer came back: {answer}",
        "ja": "返ってきた答え：{answer}",
        "ko": "돌아온 답: {answer}",
        "ru": "Ответ пришёл: {answer}",
        "es": "Llegó la respuesta: {answer}",
        "pt": "A resposta voltou: {answer}",
    },
    "ASK_NO_ANSWER": {
        "zh": "我没问到回答——可能是还在忙，或者也没什么要补充的。",
        "zh-TW": "我沒問到回答——可能是還在忙，或者也沒什麼要補充的。",
        "en": "I got no answer — I may still be busy, or have nothing to add.",
        "ja": "答えが返ってこなかった——まだ手が離せないか、付け加えることがないのかも。",
        "ko": "답이 안 왔어 — 아직 바쁘거나, 딱히 덧붙일 말이 없는 걸 수도.",
        "ru": "Ответа не пришло — возможно, я ещё занята или добавить нечего.",
        "es": "No obtuve respuesta: puede que aún esté ocupada o que no tenga nada que añadir.",
        "pt": "Não recebi resposta — posso estar ocupada ou não ter o que acrescentar.",
    },

    # =======================================================================
    # mcbot_answer — @llm_tool
    # =======================================================================
    "ANSWER_SCHEMA_ERROR": {
        "zh": "没答出去：得给一句答复。",
        "zh-TW": "沒答出去：得給一句答覆。",
        "en": "Not sent — the reply text is required.",
        "ja": "答えられなかった：返事が必要。",
        "ko": "답하지 못했어: 답변 내용이 필요해.",
        "ru": "Не отправлено — нужен текст ответа.",
        "es": "No enviado: se requiere el texto de la respuesta.",
        "pt": "Não enviado — é necessário o texto da resposta.",
    },
    "ANSWER_OK": {
        "zh": "答复带到了，我接着干。",
        "zh-TW": "答覆帶到了，我接著做。",
        "en": "Answer passed on — I'll carry on.",
        "ja": "返事は渡した。私は続ける。",
        "ko": "답은 전달했어. 나는 이어서 할게.",
        "ru": "Ответ передан — я продолжу.",
        "es": "Respuesta entregada: yo sigo.",
        "pt": "Resposta repassada — eu sigo em frente.",
    },
    "ANSWER_NO_OPEN_QUESTION": {
        "zh": "现在没有等着答复的问题。",
        "zh-TW": "現在沒有等著答覆的問題。",
        "en": "There's no open question waiting for an answer right now.",
        "ja": "今、返事を待っている質問はない。",
        "ko": "지금 답을 기다리는 질문이 없어.",
        "ru": "Сейчас нет вопроса, ожидающего ответа.",
        "es": "Ahora mismo no hay ninguna pregunta esperando respuesta.",
        "pt": "Não há nenhuma pergunta esperando resposta agora.",
    },
    "ANSWER_AMBIGUOUS_QUESTION": {
        "zh": "有好几个问题在等答复，分不清答的是哪一个。先问清楚，再把答复带过去。",
        "zh-TW": "有好幾個問題在等答覆，分不清要答的是哪一個。先問清楚，再把答覆帶過去。",
        "en": "Several questions are waiting for an answer, and I can't tell which one this is for. Ask which one first, then pass the answer on.",
        "ja": "返事待ちの質問が複数あって、どれへの返事か分からない。先に確認してから伝えて。",
        "ko": "답을 기다리는 질문이 여러 개라 어떤 질문에 대한 답인지 모르겠어. 먼저 확인하고 전해줘.",
        "ru": "Сразу несколько вопросов ждут ответа, и непонятно, к какому это относится. Сначала уточни, потом передай ответ.",
        "es": "Hay varias preguntas esperando respuesta y no sé a cuál corresponde. Pregunta primero cuál es y luego pásala.",
        "pt": "Há várias perguntas esperando resposta e não sei a qual esta se refere. Pergunte primeiro qual é e depois repasse.",
    },
    "ANSWER_UNKNOWN_QUESTION": {
        "zh": "那个问题已经过期了（我可能已经不等了）。等我下一次问的时候再回。",
        "zh-TW": "那個問題已經過期了（我可能已經不等了）。等我下一次問的時候再回。",
        "en": "That question has expired — I'm probably no longer waiting on it. Answer the next one I ask.",
        "ja": "その質問はもう終わっている（私はもう待っていないかも）。次に聞いたときに答えて。",
        "ko": "그 질문은 이미 지났어 (나는 더 이상 기다리지 않을 수도). 다음에 내가 물으면 답해줘.",
        "ru": "Этот вопрос уже неактуален — вероятно, я больше не жду. Ответь на следующий.",
        "es": "Esa pregunta ya caducó: probablemente ya no la estoy esperando. Responde a la próxima.",
        "pt": "Essa pergunta expirou — provavelmente já não estou esperando. Responda à próxima.",
    },

    # =======================================================================
    # mcbot_cancel — @llm_tool
    # =======================================================================
    "CANCEL_OK": {
        "zh": "停止指令发出去了，我正在收尾。",
        "zh-TW": "停止指令發出去了，我正在收尾。",
        "en": "Stop sent — I'm winding down.",
        "ja": "止まるように伝えた。今片付け中。",
        "ko": "멈추라고 전했어. 지금 정리하는 중이야.",
        "ru": "Команда остановиться отправлена — я сворачиваюсь.",
        "es": "Envié la orden de parar: estoy terminando.",
        "pt": "Mandei parar — estou encerrando.",
    },
    "CANCEL_NOTHING": {
        "zh": "我手上本来就没活，不用取消。",
        "zh-TW": "我手上本來就沒活，不用取消。",
        "en": "I'm not doing anything, so there's nothing to cancel.",
        "ja": "そもそも何もしていないから、中止するものはない。",
        "ko": "애초에 하는 일이 없어서 취소할 게 없어.",
        "ru": "Она ничего не делает — отменять нечего.",
        "es": "No está haciendo nada, así que no hay nada que cancelar.",
        "pt": "Ela não está fazendo nada, então não há o que cancelar.",
    },
    "CANCEL_REFUSED": {
        "zh": "她没接受这次取消，手上的活可能还在跑，我不确定它停没停。",
        "zh-TW": "她沒接受這次取消，手上的活可能還在跑，我不確定它停沒停。",
        "en": "She didn't accept the cancel, so the task may still be running — I can't tell whether it stopped.",
        "ja": "取り消しは受け付けられなかった。作業はまだ続いているかもしれず、止まったかは分からない。",
        "ko": "취소를 받아들이지 않았어. 작업이 아직 돌고 있을지도 몰라서, 멈췄는지 나도 모르겠어.",
        "ru": "Она не приняла отмену, так что задача, возможно, ещё идёт — не знаю, остановилась ли она.",
        "es": "No aceptó la cancelación, así que la tarea podría seguir en marcha; no sé si se detuvo.",
        "pt": "Ela não aceitou o cancelamento, então a tarefa pode continuar rodando — não sei se parou.",
    },
    "CANCEL_ID_ERROR": {
        "zh": "取消要指定一个任务编号（非负整数），或者留空取消当前全部。",
        "zh-TW": "取消要指定一個任務編號（非負整數），或者留空取消目前全部。",
        "en": "A cancel needs a task id (a non-negative integer), or leave it out to cancel everything running.",
        "ja": "取り消しにはタスク番号（0以上の整数）を指定するか、省略して全部取り消して。",
        "ko": "취소하려면 작업 번호(0 이상의 정수)를 주거나, 비워서 전부 취소해.",
        "ru": "Для отмены нужен номер задачи (неотрицательное целое) или пусто — тогда отменится всё.",
        "es": "Para cancelar necesito un id de tarea (entero no negativo), o déjalo vacío para cancelar todo.",
        "pt": "Para cancelar preciso de um id de tarefa (inteiro não negativo), ou deixe vazio para cancelar tudo.",
    },

    # =======================================================================
    # mcbot_status — @plugin_entry
    # =======================================================================
    "LABEL_CONNECTED": {
        "zh": "已连上",
        "zh-TW": "已連上",
        "en": "connected",
        "ja": "接続済み",
        "ko": "연결됨",
        "ru": "подключено",
        "es": "conectado",
        "pt": "conectado",
    },
    "LABEL_DISCONNECTED": {
        "zh": "未连上",
        "zh-TW": "未連上",
        "en": "not connected",
        "ja": "未接続",
        "ko": "연결 안 됨",
        "ru": "не подключено",
        "es": "sin conexión",
        "pt": "sem conexão",
    },
    "LABEL_IDLE": {
        "zh": "空闲",
        "zh-TW": "閒置",
        "en": "idle",
        "ja": "待機中",
        "ko": "대기 중",
        "ru": "простой",
        "es": "inactivo",
        "pt": "ocioso",
    },
    "NO_TOKEN_HINT": {
        "zh": "还没读到 token——{{MASTER_NAME}} 得先开着 Minecraft 进过一次世界，mcbot 才会写出 bridge.token。",
        "zh-TW": "還沒讀到 token——{{MASTER_NAME}} 得先開著 Minecraft 進過一次世界，mcbot 才會寫出 bridge.token。",
        "en": "No token yet — {{MASTER_NAME}} needs to have entered a world in Minecraft once for mcbot to write bridge.token.",
        "ja": "token がまだない——{{MASTER_NAME}} が一度 Minecraft でワールドに入らないと、mcbot は bridge.token を書き出さない。",
        "ko": "아직 token 이 없어 — {{MASTER_NAME}} 이 Minecraft 에서 월드에 한 번 들어가야 mcbot 이 bridge.token 을 만들어.",
        "ru": "Токена ещё нет — {{MASTER_NAME}} должен хотя бы раз войти в мир Minecraft, чтобы mcbot создал bridge.token.",
        "es": "Aún no hay token: {{MASTER_NAME}} debe haber entrado una vez en un mundo de Minecraft para que mcbot escriba bridge.token.",
        "pt": "Ainda não há token — {{MASTER_NAME}} precisa ter entrado uma vez em um mundo no Minecraft para o mcbot gravar bridge.token.",
    },
    "STATUS_SUMMARY": {
        "zh": "同伴 {connected_label} | 当前任务：{task} | 待答问题：{questions}",
        "zh-TW": "同伴 {connected_label} | 目前任務：{task} | 待答問題：{questions}",
        "en": "Companion {connected_label} | current task: {task} | open questions: {questions}",
        "ja": "相棒 {connected_label} | 実行中のタスク：{task} | 未回答の質問：{questions}",
        "ko": "동료 {connected_label} | 현재 작업: {task} | 답변 대기 질문: {questions}",
        "ru": "Спутница {connected_label} | текущая задача: {task} | без ответа: {questions}",
        "es": "Compañero {connected_label} | tarea actual: {task} | preguntas abiertas: {questions}",
        "pt": "Companheiro {connected_label} | tarefa atual: {task} | perguntas abertas: {questions}",
    },

    # =======================================================================
    # Transport-error rewrites. Raw failures ("ConnectError", "HTTP 401")
    # must never reach the model — the character would start narrating
    # reconnection. Each maps a McbotBridgeError code to something she can
    # say in-world.
    # =======================================================================
    # -- done report clipped before it reaches the model -----------------------
    # The cue is the *only* channel for a done report (the tool call returns an
    # empty envelope), so a hard cut is a silent loss of the tail. Say so.
    "DONE_TEXT_TRUNCATED": {
        "zh": "\n…（回执太长，后半段已省略）",
        "zh-TW": "\n…（回執太長，後半段已省略）",
        "en": "\n... (the report was too long; the rest is omitted)",
        "ja": "\n…（控えが長すぎるため、後半は省略）",
        "ko": "\n…(접수 내용이 너무 길어 뒷부분은 생략)",
        "ru": "\n… (отчёт слишком длинный; остальное опущено)",
        "es": "\n… (el informe era demasiado largo; se omite el resto)",
        "pt": "\n… (o relatório era longo demais; o restante foi omitido)",
    },
    "ERR_UNREACHABLE": {
        "zh": "现在找不到同伴——她的世界还没开，或者刚断开。",
        "zh-TW": "現在找不到同伴——她的世界還沒開，或者剛斷開。",
        "en": "Can't find your companion right now — her world isn't open, or she just dropped off.",
        "ja": "今は相棒が見つからない——ワールドが開いていないか、ちょうど抜けたところ。",
        "ko": "지금 동료를 찾을 수 없어 — 월드가 열려 있지 않거나 방금 빠졌어.",
        "ru": "Сейчас спутницу не найти — её мир закрыт или она только что вышла.",
        "es": "No encuentro al compañero ahora: su mundo no está abierto o acaba de salir.",
        "pt": "Não encontro o companheiro agora — o mundo dela não está aberto ou ela acabou de sair.",
    },
    "ERR_UNAUTHORIZED": {
        "zh": "这门不认我了——通行口令对不上，可能要重开一次世界。",
        "zh-TW": "這門不認我了——通行口令對不上，可能要重開一次世界。",
        "en": "The door won't recognise me — the passphrase doesn't match. Re-entering the world should fix it.",
        "ja": "門が通してくれない——合言葉が合っていない。ワールドに入り直せば直るはず。",
        "ko": "문이 나를 못 알아봐 — 암호가 맞지 않아. 월드에 다시 들어가면 될 거야.",
        "ru": "Дверь меня не узнаёт — пароль не совпадает. Повторный вход в мир должен помочь.",
        "es": "La puerta no me reconoce: la contraseña no coincide. Volver a entrar al mundo debería arreglarlo.",
        "pt": "A porta não me reconhece — a senha não confere. Entrar de novo no mundo deve resolver.",
    },
    "ERR_HTTP": {
        "zh": "同伴那边出了点乱子（HTTP {status}），这事没办成。",
        "zh-TW": "同伴那邊出了點亂子（HTTP {status}），這事沒辦成。",
        "en": "Something went wrong on your companion's side (HTTP {status}) — that didn't go through.",
        "ja": "相棒側で問題が起きた（HTTP {status}）。これは通らなかった。",
        "ko": "동료 쪽에서 문제가 생겼어 (HTTP {status}). 이건 안 됐어.",
        "ru": "На стороне спутницы что-то пошло не так (HTTP {status}) — не прошло.",
        "es": "Algo falló del lado del compañero (HTTP {status}): eso no pasó.",
        "pt": "Algo deu errado no lado do companheiro (HTTP {status}) — isso não passou.",
    },
}

# ===========================================================================
# Bridge error codes (v1.0 ``error_code``), mapped to something the character
# can say. The bridge's own ``error`` text is prose written for operators and
# is never forwarded — the model would quote it into the conversation, and it
# can echo request detail.
# ===========================================================================

BRIDGE_ERROR_MESSAGES: Dict[str, Dict[str, str]] = {
    "UNAUTHORIZED": {
        "zh": "这门不认我了——通行口令对不上，可能要重开一次世界。",
        "zh-TW": "這門不認我了——通行口令對不上，可能要重開一次世界。",
        "en": "The door won't recognise me — the passphrase doesn't match. Re-entering the world should fix it.",
        "ja": "門が通してくれない——合言葉が合っていない。ワールドに入り直せば直るはず。",
        "ko": "문이 나를 못 알아봐 — 암호가 맞지 않아. 월드에 다시 들어가면 될 거야.",
        "ru": "Дверь меня не узнаёт — пароль не совпадает. Повторный вход в мир должен помочь.",
        "es": "La puerta no me reconoce: la contraseña no coincide. Volver a entrar al mundo debería arreglarlo.",
        "pt": "A porta não me reconhece — a senha não confere. Entrar de novo no mundo deve resolver.",
    },
    "ANSWER_TIMEOUT": {
        "zh": "等同伴回话等太久了。她那边可能还在忙，也可能已经错过了。",
        "zh-TW": "等同伴回話等太久了。她那邊可能還在忙，也可能已經錯過了。",
        "en": "Waited too long for the companion to answer. She may still be busy, or the moment has passed.",
        "ja": "相棒の返事を待ちすぎた。まだ手が離せないか、機を逃したかもしれない。",
        "ko": "동료의 대답을 너무 오래 기다렸어. 아직 바쁘거나, 때를 놓쳤을 수 있어.",
        "ru": "Слишком долго ждала ответа спутницы. Возможно, она занята или момент упущен.",
        "es": "Esperé demasiado la respuesta del compañero. Puede seguir ocupado o haber pasado el momento.",
        "pt": "Esperei demais pela resposta do companheiro. Pode ainda estar ocupado, ou a hora passou.",
    },
    "TASK_FAILED": {
        "zh": "同伴那件事没正常做完。",
        "zh-TW": "同伴那件事沒正常做完。",
        "en": "That didn't finish properly on your companion's side.",
        "ja": "相棒側でその件が正常に終わらなかった。",
        "ko": "동료 쪽에서 그 일이 제대로 끝나지 않았어.",
        "ru": "У спутницы это не завершилось нормально.",
        "es": "Eso no terminó bien del lado del compañero.",
        "pt": "Isso não terminou direito do lado do companheiro.",
    },
    "BODY_TOO_LARGE": {
        "zh": "这句话太长了，递不过去——说短一点再来一次。",
        "zh-TW": "這句話太長了，遞不過去——說短一點再來一次。",
        "en": "That was too long to pass along — shorten it and try again.",
        "ja": "長すぎて渡せなかった——短くしてもう一度。",
        "ko": "너무 길어서 전달하지 못했어 — 짧게 줄여서 다시 해줘.",
        "ru": "Слишком длинно, чтобы передать — сократи и попробуй снова.",
        "es": "Era demasiado largo para pasarlo: acórtalo e inténtalo otra vez.",
        "pt": "Era longo demais para repassar — encurte e tente de novo.",
    },
    "NOT_FOUND": {
        "zh": "同伴那边找不到对应的东西了（可能已经过期）。",
        "zh-TW": "同伴那邊找不到對應的東西了（可能已經過期）。",
        "en": "Your companion has nothing matching that any more — it has probably expired.",
        "ja": "相棒側にもう該当するものが無い（おそらく期限切れ）。",
        "ko": "동료 쪽에 더는 해당하는 게 없어 (아마 만료됐을 거야).",
        "ru": "У спутницы больше нет ничего подходящего — вероятно, истекло.",
        "es": "El compañero ya no tiene nada que corresponda: probablemente caducó.",
        "pt": "O companheiro não tem mais nada correspondente — provavelmente expirou.",
    },
    "INVALID_REQUEST": {
        "zh": "这条请求同伴没看懂，没能接。换个说法再来一次。",
        "zh-TW": "這條請求同伴沒看懂，沒能接。換個說法再來一次。",
        "en": "Your companion couldn't make sense of that request, so it wasn't accepted. Rephrase and retry.",
        "ja": "その依頼は相棒に通じず、受け付けられなかった。言い方を変えて再試行して。",
        "ko": "그 요청을 동료가 이해하지 못해 받아들이지 않았어. 다르게 말해서 다시 시도해.",
        "ru": "Спутница не разобрала запрос, он не принят. Переформулируй и повтори.",
        "es": "El compañero no entendió la petición, así que no se aceptó. Reformúlala y reintenta.",
        "pt": "O companheiro não entendeu o pedido, então não foi aceito. Reformule e tente de novo.",
    },
    "INTERNAL": {
        "zh": "同伴那边出了点乱子，这件事到底做没做还不知道。",
        "zh-TW": "同伴那邊出了點亂子，這件事到底做沒做還不知道。",
        "en": "Something went wrong on your companion's side, and whether it happened is unknown.",
        "ja": "相棒側で問題が起きた。実行されたかどうかは分からない。",
        "ko": "동료 쪽에서 문제가 생겼어. 실행됐는지는 알 수 없어.",
        "ru": "На стороне спутницы что-то пошло не так; выполнено ли — неизвестно.",
        "es": "Algo falló del lado del compañero y no se sabe si llegó a ocurrir.",
        "pt": "Algo deu errado no lado do companheiro, e não se sabe se chegou a acontecer.",
    },
    # Client-side, not from the bridge.
    "BRIDGE_NOT_STARTED": {
        "zh": "现在找不到同伴——她的世界还没开，或者刚断开。",
        "zh-TW": "現在找不到同伴——她的世界還沒開，或者剛斷開。",
        "en": "Can't find your companion right now — her world isn't open, or she just dropped off.",
        "ja": "今は相棒が見つからない——ワールドが開いていないか、ちょうど抜けたところ。",
        "ko": "지금 동료를 찾을 수 없어 — 월드가 열려 있지 않거나 방금 빠졌어.",
        "ru": "Сейчас спутницу не найти — её мир закрыт или она только что вышла.",
        "es": "No encuentro al compañero ahora: su mundo no está abierto o acaba de salir.",
        "pt": "Não encontro o companheiro agora — o mundo dela não está aberto ou ela acabou de sair.",
    },
}

# Fallback for a code this plugin does not know: the bridge may add categories,
# and a connector must not choke on one it has not seen.
_BRIDGE_ERROR_FALLBACK: Dict[str, str] = {
    "zh": "同伴那边出了点状况（{status}），这件事没办成。",
    "zh-TW": "同伴那邊出了點狀況（{status}），這件事沒辦成。",
    "en": "Something went wrong on your companion's side ({status}) — that didn't go through.",
    "ja": "相棒側で問題が起きた（{status}）。これは通らなかった。",
    "ko": "동료 쪽에서 문제가 생겼어 ({status}). 이건 안 됐어.",
    "ru": "На стороне спутницы что-то пошло не так ({status}) — не прошло.",
    "es": "Algo falló del lado del compañero ({status}): eso no pasó.",
    "pt": "Algo deu errado no lado do companheiro ({status}) — isso não passou.",
}


def bridge_error(code: str, *, lang: str | None = None, status: Any = None) -> str:
    """Resolve a bridge ``error_code`` into a line the character can say.

    Unknown codes fall through to a generic message carrying the HTTP status,
    so a bridge that grows a new category degrades to "something went wrong"
    rather than leaking an internal code to the model.
    """
    bundle = BRIDGE_ERROR_MESSAGES.get(code)
    lang_key = lang or DEFAULT_LANG
    if bundle is None:
        text = _BRIDGE_ERROR_FALLBACK.get(lang_key)
        if not text and lang_key.startswith("zh"):
            text = _BRIDGE_ERROR_FALLBACK["zh"]
        text = text or _BRIDGE_ERROR_FALLBACK[DEFAULT_LANG]
        return text.format(status=status if status is not None else "?")

    text = bundle.get(lang_key)
    if not text and lang_key.startswith("zh"):
        text = bundle.get("zh")
    return text or bundle[DEFAULT_LANG]


def gag(lang: str | None = None) -> str:
    """Resolve the shared "don't narrate internals" tail for a locale.

    Kept out of ``PROMPTS`` because the cue templates reference it as a
    ``{internal_gag}`` *value* rather than resolving it themselves — the caller
    formats each cue with ``internal_gag=gag(lang)`` so the same tail can be
    swapped across every cue from one place.
    """
    lang_key = lang or DEFAULT_LANG
    text = _INTERNAL_STATE_GAG.get(lang_key)
    if not text and lang_key.startswith("zh"):
        text = _INTERNAL_STATE_GAG["zh"]
    text = text or _INTERNAL_STATE_GAG[DEFAULT_LANG]
    return text.replace("{{MASTER_NAME}}", "{MASTER_NAME}")


__all__ = [
    "PROMPTS",
    "BRIDGE_ERROR_MESSAGES",
    "DEFAULT_LANG",
    "SUPPORTED_LANGS",
    "t",
    "gag",
    "bridge_error",
    "user_lang",
]
