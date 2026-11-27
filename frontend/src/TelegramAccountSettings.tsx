import { useEffect, useRef, useState, type FormEvent } from "react";
import { ShieldCheck } from "lucide-react";
import { call, desktop } from "./api";
import { Button } from "./components/ui/button";
import "./telegram-downloads.css";

type ProxyMode = "inherit" | "direct" | "socks5" | "http" | "existing";
const proxyOptions: Record<ProxyMode, string> = {
  inherit: "沿用 X 的代理",
  direct: "直连",
  socks5: "SOCKS5",
  http: "HTTP CONNECT",
  existing: "保留现有代理",
};
type Account = {
  configured: boolean;
  authorized: boolean;
  proxy_mode?: ProxyMode;
  proxy_port?: number | null;
  code_sent?: boolean;
  password_required?: boolean;
  code_delivery?: string | null;
  code_can_resend?: boolean;
  code_resend_at?: number | null;
};
const codeDestinations: Record<string, string> = {
  app: "请在已登录的手机或电脑上，打开 Telegram 的官方「Telegram」会话查看验证码。",
  sms: "Telegram 已选择短信投递，请查看手机短信。",
  sms_text: "请查看手机短信，并输入收到的单词或短语。",
  email: "请查看 Telegram 账号绑定的登录邮箱。",
  call: "请接听 Telegram 的验证电话。",
  missed_call: "请查看验证来电，按 Telegram 的提示输入来电号码中的验证码。",
  fragment: "请在 Telegram 指定的 Fragment 账号中查看验证码。",
  email_setup: "Telegram 要求先设置登录邮箱，请在官方客户端完成后再试。",
  official_sms: "此短信验证方式需要 Telegram 官方手机客户端。",
};
type QrLogin = {
  state: "idle" | "waiting" | "expired" | "password" | "authorized" | "failed";
  expires_at?: number;
  image?: string;
  error?: string;
};
type QrPanel = QrLogin | { state: "starting" };

type Props = { onAccountChanged: () => void };

// A reopened panel waits for any QR cancellation from the previous mount.
let qrOperation: Promise<unknown> = Promise.resolve();

function AccountPanel({ onAccountChanged }: Props) {
  const [account, setAccount] = useState<Account | null>(null);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [qr, setQr] = useState<QrPanel | null>(null);
  const qrSelected = useRef(false);
  const qrAttempt = useRef(0);
  const proxyDetails = useRef<HTMLDetailsElement>(null);
  const [manualCredentials, setManualCredentials] = useState(false);
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [proxyDraft, setProxyDraft] = useState<{
    mode: ProxyMode;
    port: string;
  } | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [now, setNow] = useState(Date.now);
  const codeSent = account?.code_sent;
  const savedProxyMode = account?.proxy_mode ?? "inherit";
  const proxyMode = proxyDraft?.mode ?? savedProxyMode;
  const proxyPort = proxyDraft?.port ?? String(account?.proxy_port ?? "");
  const localProxy = proxyMode === "socks5" || proxyMode === "http";
  const delivery = account?.code_delivery ?? "";
  const codeRequiresOfficialApp =
    delivery === "email_setup" || delivery === "official_sms";
  const canResend =
    codeSent && !account?.password_required && account?.code_can_resend;
  const resendAt = account?.code_resend_at ?? 0;
  const resendSeconds = Math.max(0, Math.ceil((resendAt - now) / 1000));
  const qrExpiresAt = qr?.state === "waiting" ? (qr.expires_at ?? 0) : 0;
  const qrSeconds = Math.max(0, Math.ceil((qrExpiresAt - now) / 1000));
  const countdownAt = Math.max(canResend ? resendAt : 0, qrExpiresAt);

  async function run(action: string, operation: () => Promise<void>) {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "操作失败，请重试");
      if (
        [
          "tg_send_code",
          "tg_resend_code",
          "tg_reset_code",
          "tg_sign_in",
        ].includes(action)
      ) {
        const latest = await call<Account>("tg_status").catch(() => null);
        if (latest) setAccount(latest);
      }
    } finally {
      setBusy("");
    }
  }

  function cancelQr() {
    if (!qrSelected.current) return qrOperation;
    qrSelected.current = false;
    qrAttempt.current += 1;
    const cancellation = qrOperation
      .catch(() => undefined)
      .then(() => call<QrLogin>("tg_qr_cancel"));
    qrOperation = cancellation;
    return cancellation;
  }

  async function closeQr() {
    setQr(null);
    setPassword("");
    try {
      await cancelQr();
    } catch {
      // The local flow is closed even if the account connection is unavailable.
    }
  }

  async function updateQr(result: QrLogin, attempt: number, keepImage = false) {
    if (attempt !== qrAttempt.current) return;
    if (result.state === "authorized") {
      const latest = await call<Account>("tg_status");
      if (attempt !== qrAttempt.current) return;
      if (!latest.authorized)
        throw new Error("Telegram 尚未确认登录，请重试。");
      qrSelected.current = false;
      setQr(null);
      setAccount(latest);
      clearAccountInputs();
      onAccountChanged();
      return;
    }
    if (result.state === "waiting" && keepImage) {
      setQr((current) =>
        current?.state === "waiting"
          ? { ...current, expires_at: result.expires_at ?? current.expires_at }
          : current,
      );
      return;
    }
    if (
      result.state === "waiting" &&
      (!result.image?.startsWith("data:image/png;base64,") ||
        !Number.isFinite(result.expires_at))
    ) {
      throw new Error("无法读取 Telegram 登录二维码，请重试。");
    }
    setQr(result);
  }

  function startQr() {
    if (busy || qr?.state === "starting") return;
    qrSelected.current = true;
    const attempt = ++qrAttempt.current;
    setQr({ state: "starting" });
    setNow(Date.now());
    setError("");
    setNotice("");
    setCode("");
    setPassword("");
    closeManualCredentials();
    if (proxyDetails.current) proxyDetails.current.open = false;

    const start = qrOperation
      .catch(() => undefined)
      .then(async () => {
        if (attempt !== qrAttempt.current) return;
        setAccount(
          (current) =>
            current && {
              ...current,
              code_sent: false,
              password_required: false,
            },
        );
        await updateQr(await call<QrLogin>("tg_qr_start"), attempt);
      });
    qrOperation = start;
    void start.catch((failure: unknown) => {
      if (attempt === qrAttempt.current) {
        setQr({
          state: "failed",
          error:
            failure instanceof Error
              ? failure.message
              : "无法生成 Telegram 登录二维码，请重试。",
        });
      }
    });
  }

  useEffect(() => {
    let alive = true;
    void qrOperation
      .catch(() => undefined)
      .then(() => (alive ? call<Account>("tg_status") : null))
      .then((result) => {
        if (alive && result) setAccount(result);
      })
      .catch(() => {
        if (alive) setError("无法读取 Telegram 账号状态，请重试。");
      });
    return () => {
      alive = false;
      void cancelQr().catch(() => undefined);
    };
  }, []);

  const waitingForQr = qr?.state === "waiting";
  useEffect(() => {
    if (!waitingForQr) return;
    const attempt = qrAttempt.current;
    let alive = true;
    let timer: number;
    async function poll() {
      if (!alive || attempt !== qrAttempt.current) return;
      try {
        const result = await call<QrLogin>("tg_qr_status");
        if (!alive) return;
        await updateQr(result, attempt, true);
        if (
          alive &&
          attempt === qrAttempt.current &&
          result.state === "waiting"
        ) {
          timer = window.setTimeout(poll, 700);
        }
      } catch {
        if (alive && attempt === qrAttempt.current) {
          setQr({
            state: "failed",
            error: "无法获取扫码登录状态，请刷新二维码。",
          });
        }
      }
    }
    timer = window.setTimeout(poll, 700);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [waitingForQr, qrExpiresAt]);

  useEffect(() => {
    if (!countdownAt) return;
    setNow(Date.now());
    if (countdownAt <= Date.now()) return;
    const timer = window.setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= countdownAt) window.clearInterval(timer);
    }, 250);
    return () => window.clearInterval(timer);
  }, [countdownAt]);

  function clearAccountInputs() {
    setPhone("");
    setCode("");
    setPassword("");
  }

  function closeManualCredentials() {
    setManualCredentials(false);
    setApiId("");
    setApiHash("");
  }

  function signIn(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (qr?.state === "password") {
      const attempt = qrAttempt.current;
      const payload = { password };
      setPassword("");
      void run("tg_qr_password", async () => {
        try {
          const result = await call<Account>("tg_sign_in", payload);
          if (attempt !== qrAttempt.current) return;
          if (result.authorized) {
            qrSelected.current = false;
            setQr(null);
            clearAccountInputs();
            onAccountChanged();
          }
          setAccount({ ...account, ...result });
        } catch (failure) {
          if (attempt === qrAttempt.current) throw failure;
        }
      });
      return;
    }
    if (!account?.password_required && !codeSent) {
      void run("tg_send_code", async () => {
        const result = await call<Account>("tg_send_code", {
          phone: phone.trim(),
        });
        setAccount({ ...account, ...result });
        if (result.authorized) {
          clearAccountInputs();
          onAccountChanged();
        }
      });
      return;
    }
    const payload = account?.password_required
      ? { password }
      : { code: code.trim() };
    setCode("");
    setPassword("");
    void run("tg_sign_in", async () => {
      const result = await call<Account>("tg_sign_in", payload);
      setAccount({ ...account, ...result });
      if (result.authorized) {
        clearAccountInputs();
        onAccountChanged();
      }
    });
  }

  const locked = Boolean(busy);
  const accountLabel = account?.authorized
    ? "已登录"
    : account?.configured
      ? "等待登录"
      : "未导入 API 凭证";
  let loginLabel = "发送验证码";
  let loginField = (
    <label>
      手机号
      <input
        required
        maxLength={30}
        type="tel"
        autoComplete="off"
        placeholder="+86…"
        value={phone}
        onChange={(event) => setPhone(event.target.value)}
      />
    </label>
  );
  if (qr?.state === "password" || account?.password_required) {
    loginLabel = "验证并登录";
    loginField = (
      <label>
        两步验证密码
        <input
          required
          maxLength={512}
          type="password"
          autoComplete="off"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
    );
  } else if (codeSent) {
    loginLabel = "验证并登录";
    loginField = (
      <label>
        验证码
        <input
          required
          maxLength={delivery === "sms_text" ? 80 : 20}
          type="password"
          inputMode={delivery === "sms_text" ? "text" : "numeric"}
          autoComplete="one-time-code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
      </label>
    );
  }

  return (
    <section
      className="settings-card telegram-panel"
      aria-labelledby="telegram-account"
    >
      <div className="telegram-heading">
        <h2 id="telegram-account">
          <ShieldCheck size={18} />
          Telegram 账号
        </h2>
        {account && <span className="telegram-status">{accountLabel}</span>}
      </div>
      {error && (
        <div className="telegram-error">
          <p role="alert">{error}</p>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {!account ? (
        error ? (
          <Button
            size="sm"
            variant="outline"
            disabled={locked}
            onClick={() =>
              void run("tg_status", async () =>
                setAccount(await call<Account>("tg_status")),
              )
            }
          >
            检查账号状态
          </Button>
        ) : (
          <p>正在读取账号状态…</p>
        )
      ) : account.authorized ? (
        <div className="telegram-actions">
          <Button
            size="sm"
            variant="outline"
            disabled={locked}
            onClick={() =>
              void run("tg_logout", async () => {
                setAccount({
                  ...account,
                  ...(await call<Account>("tg_logout")),
                });
                clearAccountInputs();
                onAccountChanged();
              })
            }
          >
            退出登录
          </Button>
        </div>
      ) : (
        <>
          <div className="telegram-actions">
            <Button
              size="sm"
              variant="outline"
              disabled={locked}
              onClick={() =>
                void run("tg_import", async () => {
                  await closeQr();
                  const result = await call<Account>("tg_import");
                  setAccount(result);
                  if (result.configured) {
                    clearAccountInputs();
                    closeManualCredentials();
                  }
                })
              }
            >
              {account.configured ? "重新导入 API 凭证" : "导入 API 凭证"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={locked}
              aria-expanded={manualCredentials}
              onClick={() => {
                if (manualCredentials) closeManualCredentials();
                else if (qr)
                  void run("tg_qr_cancel", async () => {
                    await closeQr();
                    setManualCredentials(true);
                  });
                else setManualCredentials(true);
              }}
            >
              {manualCredentials ? "收起手动输入" : "手动输入 API 凭证"}
            </Button>
          </div>
          {manualCredentials && (
            <form
              className="telegram-actions telegram-fields"
              onSubmit={(event) => {
                event.preventDefault();
                if (locked) return;
                void run("tg_credentials", async () => {
                  await closeQr();
                  const result = await call<Account>("tg_credentials", {
                    api_id: apiId.trim(),
                    api_hash: apiHash.trim(),
                  });
                  setAccount(result);
                  closeManualCredentials();
                  clearAccountInputs();
                  setNotice("Telegram API 凭证已保存。");
                });
              }}
            >
              <label>
                API ID
                <input
                  required
                  inputMode="numeric"
                  pattern="[0-9]{1,10}"
                  maxLength={10}
                  autoComplete="off"
                  value={apiId}
                  onChange={(event) => setApiId(event.target.value)}
                />
              </label>
              <label>
                API Hash
                <input
                  required
                  type="password"
                  pattern="[a-fA-F0-9]{32}"
                  maxLength={32}
                  autoComplete="off"
                  value={apiHash}
                  onChange={(event) => setApiHash(event.target.value)}
                />
              </label>
              <Button type="submit" variant="outline" disabled={locked}>
                保存 API 凭证
              </Button>
            </form>
          )}
          {account.configured && (
            <div
              className="telegram-actions"
              role="group"
              aria-label="Telegram 登录方式"
            >
              <Button
                type="button"
                size="sm"
                variant={qr ? "outline" : "default"}
                aria-pressed={!qr}
                disabled={locked}
                onClick={() => qr && void run("tg_qr_cancel", closeQr)}
              >
                手机号登录
              </Button>
              <Button
                type="button"
                size="sm"
                variant={qr ? "default" : "outline"}
                aria-pressed={Boolean(qr)}
                disabled={locked}
                onClick={() => !qr && startQr()}
              >
                扫码登录
              </Button>
            </div>
          )}
          {qr && qr.state !== "password" && (
            <div className="telegram-qr">
              {qr.state === "starting" && <p role="status">正在生成二维码…</p>}
              {qr.state === "waiting" && qrSeconds > 0 && (
                <>
                  <p>
                    在已登录的手机 Telegram 中，打开「设置 → 设备 →
                    连接桌面设备」，扫描下方二维码。
                  </p>
                  <img
                    alt="Telegram 登录二维码"
                    src={qr.image}
                    width={224}
                    height={224}
                  />
                  <p className="telegram-status">
                    等待扫码 · {qrSeconds} 秒后过期
                  </p>
                </>
              )}
              {(qr.state === "expired" ||
                (qr.state === "waiting" && qrSeconds === 0)) && (
                <p role="status">二维码已过期，请刷新。</p>
              )}
              {qr.state === "failed" && (
                <p className="telegram-error" role="alert">
                  {qr.error || "扫码登录失败，请重试。"}
                </p>
              )}
              {(qr.state === "idle" ||
                qr.state === "expired" ||
                qr.state === "failed" ||
                (qr.state === "waiting" && qrSeconds === 0)) && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={locked}
                  onClick={startQr}
                >
                  刷新二维码
                </Button>
              )}
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={locked}
                onClick={() => void run("tg_qr_cancel", closeQr)}
              >
                取消扫码登录
              </Button>
            </div>
          )}
          {qr?.state === "password" && (
            <p role="status">手机已确认，请输入 Telegram 两步验证密码。</p>
          )}
          {account.configured && (!qr || qr.state === "password") && (
            <form
              className="telegram-actions telegram-fields"
              onSubmit={signIn}
            >
              {(qr?.state === "password" || !codeRequiresOfficialApp) && (
                <>
                  {loginField}
                  <Button type="submit" disabled={locked}>
                    {loginLabel}
                  </Button>
                </>
              )}
              {qr?.state === "password" && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={locked}
                  onClick={() => void run("tg_qr_cancel", closeQr)}
                >
                  取消扫码登录
                </Button>
              )}
              {!qr && canResend && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={locked || resendSeconds > 0}
                  onClick={() =>
                    void run("tg_resend_code", async () => {
                      const result = await call<Account>("tg_resend_code");
                      setCode("");
                      setAccount({ ...account, ...result });
                    })
                  }
                >
                  {resendSeconds
                    ? `${resendSeconds} 秒后可重发`
                    : "重新发送验证码"}
                </Button>
              )}
              {!qr && (codeSent || account.password_required) && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={locked}
                  onClick={() =>
                    void run("tg_reset_code", async () => {
                      const result = await call<Account>("tg_reset_code");
                      clearAccountInputs();
                      setAccount({ ...account, ...result });
                    })
                  }
                >
                  更换手机号
                </Button>
              )}
            </form>
          )}
          {!qr && codeSent && !account.password_required && (
            <p role="status">
              {codeDestinations[delivery] ??
                "先查看已登录设备中的 Telegram 官方会话，再检查短信或登录邮箱。"}
              {account.code_can_resend === false &&
                " Telegram 暂未提供其他投递方式。"}
            </p>
          )}
        </>
      )}
      {account && (
        <details
          ref={proxyDetails}
          className="telegram-options"
          onToggle={(event) => {
            if (event.currentTarget.open && qr && !busy) {
              void run("tg_qr_cancel", closeQr);
            }
          }}
        >
          <summary>代理设置 · {proxyOptions[savedProxyMode]}</summary>
          <form
            className="telegram-actions telegram-fields"
            onSubmit={(event) => {
              event.preventDefault();
              if (locked || proxyMode === "existing") return;
              void run("tg_proxy", async () => {
                await closeQr();
                const result = await call<Account>(
                  "tg_proxy",
                  localProxy
                    ? { mode: proxyMode, port: Number(proxyPort) }
                    : { mode: proxyMode },
                );
                setProxyDraft(null);
                setAccount(result);
                if (account.code_sent && !result.code_sent) {
                  setCode("");
                  setPassword("");
                  setNotice("连接方式已更新，请重新发送验证码。");
                } else {
                  setNotice("Telegram 连接方式已保存。");
                }
              });
            }}
          >
            <label>
              代理方式
              <select
                value={proxyMode}
                onChange={(event) =>
                  setProxyDraft({
                    mode: event.target.value as ProxyMode,
                    port: proxyPort,
                  })
                }
              >
                {(Object.keys(proxyOptions) as ProxyMode[])
                  .filter(
                    (option) =>
                      option !== "existing" || savedProxyMode === "existing",
                  )
                  .map((option) => (
                    <option key={option} value={option}>
                      {proxyOptions[option]}
                    </option>
                  ))}
              </select>
            </label>
            {localProxy && (
              <label>
                本机代理端口
                <input
                  required
                  inputMode="numeric"
                  pattern="[0-9]{1,5}"
                  maxLength={5}
                  placeholder="例如 1080"
                  value={proxyPort}
                  onChange={(event) =>
                    setProxyDraft({ mode: proxyMode, port: event.target.value })
                  }
                />
              </label>
            )}
            <Button
              type="submit"
              variant="outline"
              disabled={locked || proxyMode === "existing"}
            >
              保存代理
            </Button>
          </form>
          {localProxy && <p>代理主机使用 127.0.0.1。</p>}
          {proxyMode === "inherit" && <p>X 未配置代理时使用直连。</p>}
        </details>
      )}
    </section>
  );
}

export function TelegramAccountSettings(props: Props) {
  return desktop ? (
    <AccountPanel {...props} />
  ) : (
    <section
      className="settings-card telegram-panel"
      aria-labelledby="telegram-account"
    >
      <div className="telegram-heading">
        <h2 id="telegram-account">
          <ShieldCheck size={18} /> Telegram 账号
        </h2>
      </div>
      <p>请在 Windows 桌面版管理 Telegram 账号。</p>
    </section>
  );
}
