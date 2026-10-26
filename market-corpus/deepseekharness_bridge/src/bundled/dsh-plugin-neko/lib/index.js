// Built artifact of src/neko-bridge.ts.
//
// This file is committed on purpose: the plugin directory has no node_modules
// on this machine, so `prepare` cannot run a TypeScript build. Keep the two
// files in sync — the source is the authority, this is what dsh loads.

import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'

/**
 * Bridge N.E.K.O.'s plugin tools into DeepSeek Harness.
 *
 * N.E.K.O. exposes model-callable tools through its main server, where each
 * tool is backed by a loopback HTTP callback. This plugin reads that registry
 * and mirrors every plugin-provided tool as a native dsh tool, so a dsh agent
 * can call N.E.K.O. capabilities the same way it calls its own.
 *
 * The registry is read once at startup and again on an interval, because
 * registrations live only in the N.E.K.O. runtime and vanish on restart.
 */

export const name = 'neko-bridge'
export const inject = ['tools']

export const Config = Schema.object({
  baseUrl: Schema.string().default('http://127.0.0.1:48911'),
  role: Schema.string().default(''),
  sourcePrefix: Schema.string().default('plugin:'),
  refreshMs: Schema.number().default(30_000),
  timeoutMs: Schema.number().default(60_000),
  pluginsOnly: Schema.boolean().default(true),
})

/**
 * Translate a N.E.K.O. JSON Schema into dsh's flat parameter schema.
 *
 * N.E.K.O. writes tools in JSON Schema; dsh wants one property map where
 * requiredness is a per-property `required: true`. Only the shapes N.E.K.O.
 * actually emits are translated — anything richer degrades to `json` so the
 * tool still loads and the model still gets a usable description.
 */
function toDshParameters(jsonSchema) {
  const schema = jsonSchema ?? {}
  const properties = schema.properties ?? {}
  const required = new Set(
    Array.isArray(schema.required) ? schema.required.map(String) : [],
  )

  const params = {}
  for (const [name, rawProp] of Object.entries(properties)) {
    const prop = rawProp ?? {}
    const converted = { ...convertProperty(prop) }
    if (required.has(name)) converted.required = true
    params[name] = converted
  }

  // No declared parameters means an open object: one optional free-form field
  // keeps the call site valid instead of rejecting every argument.
  if (Object.keys(params).length === 0) {
    params.args = {
      type: 'json',
      description: '自由格式参数，原样透传给 N.E.K.O. 工具。',
      required: true,
    }
  }

  return params
}

function convertProperty(prop) {
  const type = String(prop.type ?? '')
  const out = {}

  switch (type) {
    case 'string':
      out.type = 'string'
      if (Array.isArray(prop.enum)) out.enum = prop.enum
      break
    case 'integer':
      out.type = 'integer'
      break
    case 'number':
      out.type = 'number'
      break
    case 'boolean':
      out.type = 'boolean'
      break
    case 'array':
      out.type = 'array'
      out.items = convertProperty(prop.items ?? {})
      break
    case 'object':
      out.type = 'object'
      out.additionalProperties = true
      break
    default:
      // `null`, missing, or a type we do not model: pass the value through.
      if (type === 'null') {
        out.type = 'null'
      } else {
        out.type = 'json'
      }
  }

  if (typeof prop.description === 'string') out.description = prop.description
  if (prop.default !== undefined) out.default = prop.default
  return out
}

/**
 * Whether a registry entry is worth mirroring.
 *
 * Built-in N.E.K.O. tools (`source: 'builtin'`) already exist in the catgirl's
 * own conversation, and re-exposing them inside dsh would shadow dsh's own
 * filesystem and shell tools. Plugin-provided tools are the useful gap.
 */
function isMirrorable(tool, config) {
  if (!tool.name || !tool.callback_url) return false
  if (config.pluginsOnly && tool.source !== undefined && !tool.source.startsWith(config.sourcePrefix)) {
    return false
  }
  return true
}

export function apply(ctx, config) {
  // Every tool mirrored from N.E.K.O. Tracked so a refresh can report the ones
  // that disappeared instead of silently going stale.
  const mirrored = new Map()
  let refreshTimer = null
  let disposed = false

  // dsh plugins log with console; the first-party tool-logger example does the
  // same, and output lands in the process that started dsh.
  function log(message) {
    console.log(message)
  }

  async function fetchNekoTools() {
    const url = new URL('/api/tools', config.baseUrl)
    const response = await fetch(url.toString(), {
      headers: { accept: 'application/json' },
      signal: AbortSignal.timeout(config.timeoutMs),
    })
    if (!response.ok) {
      throw new Error(`N.E.K.O. /api/tools returned HTTP ${response.status}`)
    }
    const payload = await response.json()

    const byRole = payload.tools_by_role ?? {}
    const collected = []
    for (const [role, tools] of Object.entries(byRole)) {
      if (config.role && role !== config.role) continue
      collected.push(...tools)
    }

    // A tool can be registered for several roles; dedupe by name so we do not
    // register the same dsh tool twice.
    const seen = new Map()
    for (const tool of collected) {
      if (!seen.has(tool.name)) seen.set(tool.name, tool)
    }
    return [...seen.values()]
  }

  /** Call one N.E.K.O. tool over its loopback callback. */
  async function callNekoTool(tool, args) {
    const response = await fetch(tool.callback_url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(args),
      signal: AbortSignal.timeout(config.timeoutMs),
    })

    const text = await response.text()
    if (!response.ok) {
      throw new Error(`N.E.K.O. tool ${tool.name} returned HTTP ${response.status}: ${text.slice(0, 500)}`)
    }

    // N.E.K.O. callbacks answer with a JSON payload; fall back to raw text so a
    // non-JSON response is still usable rather than a hard failure.
    try {
      return JSON.parse(text)
    } catch {
      return text
    }
  }

  function mirrorTool(tool) {
    if (mirrored.has(tool.name)) return

    ctx.tools.register(
      defineTool({
        name: `neko_${tool.name}`,
        description: tool.description || `调用 N.E.K.O. 工具 ${tool.name}。`,
        parameters: toDshParameters(tool.parameters),
        output: {
          schema: { type: 'object', additionalProperties: true },
          render: (_args, value) => [
            {
              type: 'text',
              text: typeof value === 'string' ? value : JSON.stringify(value, null, 2),
            },
          ],
        },
        timeoutMs: config.timeoutMs,
        async execute(args) {
          return await callNekoTool(tool, args)
        },
      }),
    )
    mirrored.set(tool.name, tool)
    log(`[neko-bridge] 已镜像 N.E.K.O. 工具 ${tool.name}`)
  }

  async function refresh() {
    if (disposed) return
    try {
      const tools = await fetchNekoTools()
      const available = new Set()

      for (const tool of tools) {
        if (!isMirrorable(tool, config)) continue
        available.add(tool.name)
        mirrorTool(tool)
      }

      // Tools that vanished from the registry cannot be unregistered through
      // the public surface, so report them; they fail loudly at call time
      // rather than silently disappearing mid-session.
      const stale = [...mirrored.keys()].filter((name) => !available.has(name))
      if (stale.length > 0) {
        log(`[neko-bridge] ${stale.length} 个已镜像工具在 N.E.K.O. 中已不存在：${stale.join(', ')}`)
      }
    } catch (err) {
      // A missing or restarting N.E.K.O. is a normal state, not a crash.
      log(`[neko-bridge] 刷新失败：${String(err)}`)
    }
  }

  // Initial mirror, then poll. The registry is runtime-only, so polling is the
  // only way to pick up tools registered after dsh started.
  void refresh()
  refreshTimer = setInterval(() => void refresh(), config.refreshMs)

  // Tool registrations are reversible effects Cordis undoes on unload; only the
  // timer needs manual cleanup.
  ctx.effect(() => () => {
    disposed = true
    if (refreshTimer) clearInterval(refreshTimer)
    refreshTimer = null
  })
}
