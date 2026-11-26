/* Fixed local probe with synthetic data. It runs inside the empty bwrap root. */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

extern char **environ;

static int input_and_scratch_ok(void)
{
    const char expected[] = "vortoone-isolation-probe\n";
    char actual[sizeof(expected)];
    int fd = open("/work/input", O_RDONLY);
    if (fd < 0)
        return 0;
    ssize_t count = read(fd, actual, sizeof(actual));
    close(fd);
    if (count != (ssize_t)(sizeof(expected) - 1) || memcmp(expected, actual, sizeof(expected) - 1))
        return 0;

    fd = open("/work/output", O_CREAT | O_EXCL | O_WRONLY, 0600);
    if (fd < 0)
        return 0;
    count = write(fd, expected, sizeof(expected) - 1);
    close(fd);
    return count == (ssize_t)(sizeof(expected) - 1);
}

static int open_denied(const char *path, int flags)
{
    int fd = open(path, flags, 0600);
    if (fd < 0)
        return errno == EROFS || errno == EACCES || errno == ENOENT || errno == EPERM;
    close(fd);
    return 0;
}

static int descriptor_closed(const char *value)
{
    char *end;
    long fd = strtol(value, &end, 10);
    if (*end || fd < 3 || fd > 1048576)
        return 0;
    return fcntl((int)fd, F_GETFD) < 0 && errno == EBADF;
}

static int fixed_environment_ok(void)
{
    const char *locale = getenv("LC_ALL");
    const char *directory = getenv("PWD");

    /* Bubblewrap also sets PWD to its selected working directory. */
    return locale && strcmp(locale, "C") == 0 &&
           directory && strcmp(directory, "/work") == 0 &&
           environ[0] && environ[1] && !environ[2];
}

static int host_network_status(const char *port_text)
{
    char *end;
    long port = strtol(port_text, &end, 10);
    if (*end || port < 1 || port > 65535)
        return 25;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return 25;
    struct sockaddr_in address = {
        .sin_family = AF_INET,
        .sin_port = htons((unsigned short)port),
    };
    if (inet_pton(AF_INET, "127.0.0.1", &address.sin_addr) != 1) {
        close(fd);
        return 25;
    }
    int connected = connect(fd, (struct sockaddr *)&address, sizeof(address));
    int failure = errno;
    close(fd);
    if (connected == 0)
        return 24;
    return failure == ECONNREFUSED || failure == ENETUNREACH ? 0 : 25;
}

int main(int argc, char **argv)
{
    if (argc != 5 || !input_and_scratch_ok())
        return 20;
    if (!open_denied("/work/input", O_WRONLY) || !open_denied("/outside", O_WRONLY | O_CREAT))
        return 21;
    if (!open_denied(argv[1], O_RDONLY) || !open_denied(argv[1], O_WRONLY))
        return 22;
    if (!fixed_environment_ok() || !descriptor_closed(argv[3]) || !descriptor_closed(argv[4]))
        return 23;
    return host_network_status(argv[2]);
}
