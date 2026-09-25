import {
  Page,
  Card,
  Stack,
  Text,
  Badge,
  ActionButton,
  Divider,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type ClientInfo = {
  client_id: string
  battery?: number
  signal_strength?: number
}

type State = {
  server_running: boolean
  connected: boolean
  connected_clients: ClientInfo[]
  qr_code_data: string
  ws_url: string
  local_ip: string
  server_port: number
  status_message: string
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, actions } = props

  const testAction = actions.find((a) => a.id === "test_connection") as HostedAction | undefined

  const renderStatus = () => {
    if (!state.server_running) {
      return <Badge tone="error">{t("status.serverStopped")}</Badge>
    }
    if (state.connected && state.connected_clients.length > 0) {
      return <Badge tone="success">{t("status.connected")}</Badge>
    }
    return <Badge tone="warning">{t("status.waiting")}</Badge>
  }

  const renderClients = () => {
    if (!state.connected_clients || state.connected_clients.length === 0) {
      return <Text tone="secondary">{t("clients.empty")}</Text>
    }

    return (
      <Stack>
        {state.connected_clients.map((client) => (
          <Card key={client.client_id} size="small">
            <Stack spacing="small">
              <Text weight="bold">
                {t("clients.id")}: {client.client_id.substring(0, 8)}
              </Text>
              {client.battery !== undefined && (
                <Text tone="secondary">
                  {t("clients.battery")}: {client.battery}%
                </Text>
              )}
              {client.signal_strength !== undefined && (
                <Text tone="secondary">
                  {t("clients.signal")}: {client.signal_strength}
                </Text>
              )}
            </Stack>
          </Card>
        ))}
      </Stack>
    )
  }

  return (
    <Page title={props.plugin.name} subtitle={t("panel.subtitle")}>
      <Stack>
        <Card title={t("section.status")}>
          <Stack>
            <Stack direction="horizontal" align="center" justify="between">
              <Text weight="bold">{t("field.serverStatus")}</Text>
              {renderStatus()}
            </Stack>
            <Divider />
            <Stack direction="horizontal" align="center" justify="between">
              <Text weight="bold">{t("field.wsUrl")}</Text>
              <Text tone="secondary" size="small">
                {state.ws_url || "N/A"}
              </Text>
            </Stack>
            <Stack direction="horizontal" align="center" justify="between">
              <Text weight="bold">{t("field.localIp")}</Text>
              <Text tone="secondary">{state.local_ip || "N/A"}</Text>
            </Stack>
          </Stack>
        </Card>

        <Card title={t("section.qrCode")}>
          <Stack align="center">
            {state.qr_code_data ? (
              <>
                <div id="qrcode-container" style={{ padding: "20px" }}>
                  <img
                    src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(
                      state.qr_code_data
                    )}`}
                    alt="QR Code"
                    style={{ display: "block" }}
                  />
                </div>
                <Text tone="secondary" size="small">
                  {t("qr.instruction")}
                </Text>
                <Text tone="secondary" size="small" style={{ fontFamily: "monospace" }}>
                  {state.qr_code_data}
                </Text>
              </>
            ) : (
              <Text tone="secondary">{t("qr.unavailable")}</Text>
            )}
          </Stack>
        </Card>

        <Card title={t("section.clients")}>
          {renderClients()}
        </Card>

        <Card title={t("section.test")}>
          <Stack>
            <Text tone="secondary">{t("test.description")}</Text>
            {testAction ? (
              <ActionButton action={testAction} tone="primary">
                {t("test.button")}
              </ActionButton>
            ) : (
              <Text tone="error">{t("test.unavailable")}</Text>
            )}
          </Stack>
        </Card>
      </Stack>
    </Page>
  )
}
