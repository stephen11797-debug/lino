#include <alsa/asoundlib.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    snd_seq_t *seq;
    int port;
    int dst_client = 128;
    int dst_port = 0;
    if (argc > 1) dst_client = atoi(argv[1]);
    if (snd_seq_open(&seq, "default", SND_SEQ_OPEN_OUTPUT, 0) < 0) {
        fprintf(stderr, "open failed\n"); return 1;
    }
    snd_seq_set_client_name(seq, "StephenStudioTest");
    port = snd_seq_create_simple_port(seq, "test-out",
        SND_SEQ_PORT_CAP_WRITE | SND_SEQ_PORT_CAP_SUBS_WRITE,
        SND_SEQ_PORT_TYPE_MIDI_GENERIC);
    if (snd_seq_connect_to(seq, port, dst_client, dst_port) < 0) {
        fprintf(stderr, "connect failed\n"); return 1;
    }
    snd_seq_event_t ev;
    int notes[] = {60, 62, 64, 65, 67, 69, 71, 72};
    for (int i = 0; i < 8; i++) {
        memset(&ev, 0, sizeof(ev));
        snd_seq_ev_set_source(&ev, port);
        snd_seq_ev_set_dest(&ev, dst_client, dst_port);
        snd_seq_ev_set_noteon(&ev, 0, notes[i], 100);
        snd_seq_event_output(seq, &ev);
        snd_seq_drain_output(seq);
        usleep(150000);
        memset(&ev, 0, sizeof(ev));
        snd_seq_ev_set_source(&ev, port);
        snd_seq_ev_set_dest(&ev, dst_client, dst_port);
        snd_seq_ev_set_noteoff(&ev, 0, notes[i], 0);
        snd_seq_event_output(seq, &ev);
        snd_seq_drain_output(seq);
        usleep(150000);
    }
    printf("Sent 8 notes (C major scale) to %d:%d\n", dst_client, dst_port);
    return 0;
}
