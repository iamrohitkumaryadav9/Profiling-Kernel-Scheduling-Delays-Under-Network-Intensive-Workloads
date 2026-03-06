/*
 * 24_busy_poll_echo_server.c — Minimal TCP echo server with SO_BUSY_POLL
 *
 * Used for E15/E16 experiments where SO_BUSY_POLL per-socket setsockopt
 * is needed. memcached does not expose this, so we use this custom server
 * to isolate the busy-poll effect cleanly.
 *
 * Build:  gcc -O2 -o 24_busy_poll_echo_server 24_busy_poll_echo_server.c -lpthread
 * Usage:  ./24_busy_poll_echo_server <bind_ip> <port> [busy_poll_us]
 *         busy_poll_us defaults to 50 if not specified.
 *
 * Protocol: Reads up to 4KB, echoes back. Handles multiple clients via threads.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define BACKLOG 128
#define BUFSIZE 4096

static int g_busy_poll_us = 50;

void *handle_client(void *arg) {
    int fd = *(int *)arg;
    free(arg);

    /* Set SO_BUSY_POLL on the accepted socket */
    if (setsockopt(fd, SOL_SOCKET, SO_BUSY_POLL,
                   &g_busy_poll_us, sizeof(g_busy_poll_us)) < 0) {
        perror("setsockopt SO_BUSY_POLL (client)");
        /* Non-fatal: continue without busy poll */
    }

    char buf[BUFSIZE];
    ssize_t n;
    while ((n = read(fd, buf, BUFSIZE)) > 0) {
        ssize_t written = 0;
        while (written < n) {
            ssize_t w = write(fd, buf + written, n - written);
            if (w <= 0) goto done;
            written += w;
        }
    }
done:
    close(fd);
    return NULL;
}

int main(int argc, char *argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <bind_ip> <port> [busy_poll_us]\n", argv[0]);
        return 1;
    }

    const char *bind_ip = argv[1];
    int port = atoi(argv[2]);
    if (argc >= 4) g_busy_poll_us = atoi(argv[3]);

    int listenfd = socket(AF_INET, SOCK_STREAM, 0);
    if (listenfd < 0) { perror("socket"); return 1; }

    int opt = 1;
    setsockopt(listenfd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    /* Set SO_BUSY_POLL on the listening socket */
    if (setsockopt(listenfd, SOL_SOCKET, SO_BUSY_POLL,
                   &g_busy_poll_us, sizeof(g_busy_poll_us)) < 0) {
        perror("setsockopt SO_BUSY_POLL (listen)");
        /* Non-fatal */
    }

    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(port),
    };
    inet_pton(AF_INET, bind_ip, &addr.sin_addr);

    if (bind(listenfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind"); return 1;
    }
    if (listen(listenfd, BACKLOG) < 0) {
        perror("listen"); return 1;
    }

    fprintf(stderr, "[busy_poll_echo] Listening on %s:%d (busy_poll=%d us)\n",
            bind_ip, port, g_busy_poll_us);

    while (1) {
        int *client_fd = malloc(sizeof(int));
        *client_fd = accept(listenfd, NULL, NULL);
        if (*client_fd < 0) { free(client_fd); continue; }

        pthread_t tid;
        pthread_create(&tid, NULL, handle_client, client_fd);
        pthread_detach(tid);
    }

    return 0;
}
