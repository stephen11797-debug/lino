#include <webkit2/webkit2.h>
#include <gtk/gtk.h>
#include <libsoup/soup.h>
#include <alsa/asoundlib.h>
#include <sys/stat.h>
#include <string.h>
#include <stdlib.h>

static WebKitWebView *g_view = NULL;
static snd_seq_t *g_seq = NULL;
static int g_midi_port = -1;
static int g_connected = 0;

static void on_download_started(WebKitDownload *download, gpointer d) {
    const char *home = g_get_home_dir();
    gchar *dir = g_build_filename(home, "Documents", "Stephens Recordings", NULL);
    g_mkdir_with_parents(dir, 0755);
    WebKitURIRequest *req = webkit_download_get_request(download);
    const char *uri = req ? webkit_uri_request_get_uri(req) : NULL;
    gchar *name = uri ? g_path_get_basename(uri) : g_strdup("recording.webm");
    gchar *dest = g_build_filename(dir, name, NULL);
    g_print("SAVING RECORDING: %s\n", dest);
    webkit_download_set_destination(download, dest);
    g_free(dest); g_free(name); g_free(dir);
}

static void probe_done(WebKitWebView *v, GAsyncResult *r, gpointer p) {
    WebKitJavascriptResult *jr = webkit_web_view_run_javascript_finish(v, r, NULL);
    if (jr) {
        JSCValue *val = webkit_javascript_result_get_js_value(jr);
        char *s = jsc_value_to_string(val);
        g_print("PROBE: %s\n", s);
        g_free(s);
    }
    webkit_javascript_result_unref(jr);
}

static gboolean on_permission_request(WebKitWebView *view,
                                      WebKitPermissionRequest *request,
                                      gpointer data) {
    g_print("ALLOW PERMISSION: %s\n", G_OBJECT_TYPE_NAME(request));
    webkit_permission_request_allow(request);
    return TRUE;
}

/* ---------------- MIDI bridge ---------------- */
static void js_eval(const char *js) {
    if (g_view) webkit_web_view_run_javascript(g_view, js, NULL, NULL, NULL);
}

static void connect_midi_sources(void) {
    snd_seq_client_info_t *cinfo;
    snd_seq_port_info_t *pinfo;
    snd_seq_client_info_alloca(&cinfo);
    snd_seq_port_info_alloca(&pinfo);
    int found = 0;
    snd_seq_client_info_set_client(cinfo, -1);
    while (snd_seq_query_next_client(g_seq, cinfo) >= 0) {
        int c = snd_seq_client_info_get_client(cinfo);
        snd_seq_port_info_set_client(pinfo, c);
        snd_seq_port_info_set_port(pinfo, -1);
        while (snd_seq_query_next_port(g_seq, pinfo) >= 0) {
            unsigned int cap = snd_seq_port_info_get_capability(pinfo);
            int p = snd_seq_port_info_get_port(pinfo);
            int type = snd_seq_port_info_get_type(pinfo);
            if ((cap & SND_SEQ_PORT_CAP_READ) &&
                (cap & SND_SEQ_PORT_CAP_SUBS_READ)) {
                if (snd_seq_connect_from(g_seq, g_midi_port, c, p) == 0) {
                    g_print("MIDI connected: %d:%d %s\n", c, p,
                            snd_seq_port_info_get_name(pinfo));
                    found++;
                }
            }
            (void)type;
        }
    }
    if (found != g_connected) {
        g_connected = found;
        gchar *js = g_strdup_printf(
            "window.onMidiDevices&&window.onMidiDevices(%d)", found);
        js_eval(js);
        g_free(js);
    }
}

static gboolean on_midi_fd(GIOChannel *ch, GIOCondition cond, gpointer d) {
    snd_seq_event_t *ev;
    int rc;
    while ((rc = snd_seq_event_input(g_seq, &ev)) >= 0) {
        if (rc == 0) continue;
        if (snd_seq_ev_is_note_type(ev)) {
            int on = (ev->type == SND_SEQ_EVENT_NOTEON &&
                      ev->data.note.velocity > 0);
            gchar *js = g_strdup_printf(
                "window.onNativeMidi&&window.onNativeMidi(%d,%d,%d)",
                ev->data.note.note, ev->data.note.velocity, on);
            js_eval(js);
            g_free(js);
        } else if (ev->type == SND_SEQ_EVENT_PORT_START ||
                   ev->type == SND_SEQ_EVENT_PORT_EXIT) {
            connect_midi_sources();
        }
    }
    return TRUE;
}

static void init_midi(void) {
    if (snd_seq_open(&g_seq, "default", SND_SEQ_OPEN_INPUT, 0) < 0) {
        g_print("MIDI: cannot open sequencer\n");
        return;
    }
    snd_seq_set_client_name(g_seq, "StephenStudioMIDI");
    g_midi_port = snd_seq_create_simple_port(
        g_seq, "midi-in",
        SND_SEQ_PORT_CAP_WRITE | SND_SEQ_PORT_CAP_SUBS_WRITE,
        SND_SEQ_PORT_TYPE_MIDI_GENERIC | SND_SEQ_PORT_TYPE_APPLICATION);
    if (g_midi_port < 0) { g_print("MIDI: cannot create port\n"); return; }
    snd_seq_nonblock(g_seq, 1);

    int nfds;
    struct pollfd pfds[4];
    nfds = snd_seq_poll_descriptors(g_seq, pfds, 4, POLLIN);
    g_print("MIDI: sequencer ready (%d fd)\n", nfds);
    if (nfds > 0) {
        GIOChannel *ch = g_io_channel_unix_new(pfds[0].fd);
        g_io_add_watch(ch, G_IO_IN, on_midi_fd, NULL);
        g_io_channel_unref(ch);
    }
    connect_midi_sources();
}

/* ---------------- AI bridge (ollama via local HTTP) ---------------- */
static void js_eval_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char *js = g_strdup_vprintf(fmt, ap);
    va_end(ap);
    js_eval(js);
    g_free(js);
}

static char *js_escape(const char *s) {
    GString *out = g_string_new("\"");
    for (const char *p = s; *p; p++) {
        switch (*p) {
            case '\\': g_string_append(out, "\\\\"); break;
            case '"': g_string_append(out, "\\\""); break;
            case '\n': g_string_append(out, "\\n"); break;
            case '\r': g_string_append(out, "\\r"); break;
            case '\t': g_string_append(out, "\\t"); break;
            default:
                if ((unsigned char)*p < 0x20)
                    g_string_append_printf(out, "\\u%04x", (unsigned char)*p);
                else
                    g_string_append_c(out, *p);
        }
    }
    g_string_append_c(out, '"');
    return g_string_free(out, FALSE);
}

static char *extract_content(const char *json) {
    const char *k = strstr(json, "\"content\"");
    if (!k) return NULL;
    const char *p = strchr(k, ':');
    if (!p) return NULL;
    p++;
    while (*p == ' ' || *p == '\t') p++;
    if (*p != '"') return NULL;
    p++;
    GString *out = g_string_new(NULL);
    while (*p && *p != '"') {
        if (*p == '\\' && p[1]) {
            p++;
            switch (*p) {
                case 'n': g_string_append_c(out, '\n'); break;
                case 't': g_string_append_c(out, '\t'); break;
                case 'r': g_string_append_c(out, '\r'); break;
                case '"': g_string_append_c(out, '"'); break;
                case '\\': g_string_append_c(out, '\\'); break;
                case '/': g_string_append_c(out, '/'); break;
                case 'u': {
                    char hex[5] = { p[1], p[2], p[3], p[4], 0 };
                    unsigned int cp = (unsigned int)strtoul(hex, NULL, 16);
                    char utf8[8] = {0};
                    int n = g_unichar_to_utf8(cp, utf8);
                    if (n > 0) g_string_append(out, utf8);
                    p += 4;
                    break;
                }
                default: g_string_append_c(out, *p); break;
            }
        } else {
            g_string_append_c(out, *p);
        }
        p++;
    }
    return g_string_free(out, FALSE);
}

typedef struct {
    SoupMessage *msg;
    long id;
} AiCtx;

static void ai_http_done(GObject *source, GAsyncResult *res, gpointer user_data) {
    AiCtx *ctx = user_data;
    SoupSession *session = SOUP_SESSION(source);
    GError *err = NULL;
    GBytes *bytes = soup_session_send_and_read_finish(session, res, &err);
    if (err) {
        g_print("AI: request error: %s\n", err->message);
        g_error_free(err);
        if (bytes) g_bytes_unref(bytes);
        js_eval_printf("window.onAiFail&&window.onAiFail(%ld)", ctx->id);
    } else {
        guint status = soup_message_get_status(ctx->msg);
        char *text = NULL;
        if (status == SOUP_STATUS_OK && bytes) {
            gsize len;
            const char *data = g_bytes_get_data(bytes, &len);
            char *json = g_strndup(data, len);
            text = extract_content(json);
            g_free(json);
        } else {
            g_print("AI: status %u\n", status);
        }
        if (text) {
            char *esc = js_escape(text);
            js_eval_printf("window.onAiReply&&window.onAiReply(%ld,%s)", ctx->id, esc);
            g_free(esc);
            g_free(text);
        } else {
            js_eval_printf("window.onAiFail&&window.onAiFail(%ld)", ctx->id);
        }
        if (bytes) g_bytes_unref(bytes);
    }
    if (ctx->msg) g_object_unref(ctx->msg);
    g_free(ctx);
    g_object_unref(session);
}

static void ai_post(long id, const char *body) {
    SoupSession *session = soup_session_new();
    g_object_set(session, "timeout", 180, NULL);
    SoupMessage *msg = soup_message_new("POST", "http://127.0.0.1:11434/api/chat");
    if (!msg) {
        g_object_unref(session);
        js_eval_printf("window.onAiFail&&window.onAiFail(%ld)", id);
        return;
    }
    GBytes *bytes = g_bytes_new(body, strlen(body));
    soup_message_set_request_body_from_bytes(msg, "application/json", bytes);
    g_bytes_unref(bytes);
    AiCtx *ctx = g_new0(AiCtx, 1);
    ctx->msg = msg;
    ctx->id = id;
    soup_session_send_and_read_async(session, msg, G_PRIORITY_DEFAULT, NULL, ai_http_done, ctx);
}

static void on_ai_message(WebKitUserContentManager *ucm,
                          WebKitJavascriptResult *result, gpointer user_data) {
    (void)ucm; (void)user_data;
    JSCValue *val = webkit_javascript_result_get_js_value(result);
    char *s = jsc_value_to_string(val);
    if (!s) return;
    char *nl = strchr(s, '\n');
    if (nl) {
        *nl = '\0';
        long id = atol(s);
        ai_post(id, nl + 1);
    }
    g_free(s);
}


static void on_loaded(WebKitWebView *view, WebKitLoadEvent ev, gpointer d) {
    if (ev != WEBKIT_LOAD_FINISHED) return;
    const char *probe =
        "JSON.stringify({ midi: typeof navigator.requestMIDIAccess, "
        "usb: !!navigator.usb, gpu: !!document.createElement('canvas').getContext('webgl') })";
    webkit_web_view_run_javascript(view, probe, NULL, probe_done, NULL);
    js_eval("window.onAiReady&&window.onAiReady(1)");
}

int main(int argc, char **argv) {
    gtk_init(&argc, &argv);

    GtkWidget *win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(win), "Stephens DAW & Video Studio");
    gtk_window_set_default_size(GTK_WINDOW(win), 430, 900);
    gtk_window_set_position(GTK_WINDOW(win), GTK_WIN_POS_CENTER);
    g_signal_connect(win, "destroy", G_CALLBACK(gtk_main_quit), NULL);

    g_view = WEBKIT_WEB_VIEW(webkit_web_view_new());
    g_signal_connect(g_view, "load-changed", G_CALLBACK(on_loaded), NULL);
    g_signal_connect(g_view, "permission-request", G_CALLBACK(on_permission_request), NULL);
    g_signal_connect(webkit_web_context_get_default(), "download-started",
                     G_CALLBACK(on_download_started), NULL);

    WebKitUserContentManager *ucm = webkit_web_view_get_user_content_manager(g_view);
    webkit_user_content_manager_register_script_message_handler(ucm, "ai");
    g_signal_connect(ucm, "script-message-received::ai", G_CALLBACK(on_ai_message), NULL);

    WebKitSettings *st = webkit_web_view_get_settings(g_view);
    g_object_set(st,
        "enable-webgl", TRUE,
        "enable-media-stream", TRUE,
        "enable-media", TRUE,
        "enable-developer-extras", TRUE,
        "enable-javascript", TRUE,
        NULL);
    if (g_object_class_find_property(G_OBJECT_GET_CLASS(st), "enable-experimental-features"))
        g_object_set(st, "enable-experimental-features", TRUE, NULL);

    gtk_container_add(GTK_CONTAINER(win), GTK_WIDGET(g_view));

    char path[4096];
    snprintf(path, sizeof path, "file://%s/stephen_studio.html",
             g_get_current_dir());
    webkit_web_view_load_uri(g_view, path);

    init_midi();

    gtk_widget_show_all(win);
    gtk_main();
    return 0;
}
