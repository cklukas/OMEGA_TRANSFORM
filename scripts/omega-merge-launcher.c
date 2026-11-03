/* Copyright (c) 2026 by C. Klukas
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * The program launchd starts for the daily merge (scripts/com.klukas.omega-merge.plist).
 * It runs one fixed script, scripts/merge-results.sh, and nothing else.
 *
 * Why it exists: macOS lets a program that launchd starts read an external volume
 * only with the user's permission (Full Disk Access), and the permission belongs
 * to the program launchd started; the processes it spawns run under it. Granting
 * it to this launcher instead of /bin/bash keeps every other shell script without
 * disk access. The launcher spawns the script and waits for
 * it, rather than replacing itself with it, so it stays the process macOS asks.
 *
 * Build (scripts/install-merge-job.sh does this):
 *   clang -O2 -Wall -DSCRIPT='"/path/to/merge-results.sh"' -o omega-merge omega-merge-launcher.c
 *   codesign --force --sign - --identifier com.klukas.omega-merge omega-merge
 */
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>

#ifndef SCRIPT
#error "build with -DSCRIPT='\"/absolute/path/to/merge-results.sh\"'"
#endif

extern char **environ;

int main(void) {
    char *argv[] = {"/bin/bash", SCRIPT, NULL};
    pid_t pid;
    int err = posix_spawn(&pid, "/bin/bash", NULL, NULL, argv, environ);
    if (err != 0) {
        fprintf(stderr, "omega-merge: cannot start %s: %s\n", SCRIPT, strerror(err));
        return 127;
    }
    int status;
    if (waitpid(pid, &status, 0) < 0) {
        perror("omega-merge: waitpid");
        return 127;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
