#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/landlock.h>
#include <linux/sched.h>
#include <linux/seccomp.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/mman.h>

#define DENY_SYSCALL(number) \
  BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (number), 0, 1), \
  BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA))

#define REJECT_SYSCALL(number, error_number) \
  BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (number), 0, 1), \
  BPF_STMT(BPF_RET | BPF_K, \
           SECCOMP_RET_ERRNO | ((error_number) & SECCOMP_RET_DATA))

#if defined(__x86_64__)
#define EXPECTED_AUDIT_ARCH AUDIT_ARCH_X86_64
#elif defined(__aarch64__)
#define EXPECTED_AUDIT_ARCH AUDIT_ARCH_AARCH64
#else
#error "seccomp-launcher supports only x86_64 and aarch64 Linux"
#endif

#define MINIMUM_LANDLOCK_ABI 4

static int require_landlock(void) {
#ifdef __NR_landlock_create_ruleset
  int abi = (int)syscall(__NR_landlock_create_ruleset, NULL, 0,
                         LANDLOCK_CREATE_RULESET_VERSION);
  if (abi >= MINIMUM_LANDLOCK_ABI) {
    return 0;
  }
  if (abi < 0) {
    perror("cannot query Landlock ABI");
  } else {
    fprintf(stderr, "Landlock ABI %d is below required ABI %d\n", abi,
            MINIMUM_LANDLOCK_ABI);
  }
#else
  fputs("Landlock syscall is unavailable at build time\n", stderr);
#endif
  return -1;
}

static int install_filter(void) {
  struct sock_filter filter[] = {
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, EXPECTED_AUDIT_ARCH, 1, 0),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#if defined(__x86_64__) && defined(__X32_SYSCALL_BIT)
      BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, __X32_SYSCALL_BIT, 0, 1),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
#endif
      DENY_SYSCALL(__NR_socket),
#ifdef __NR_socketpair
      DENY_SYSCALL(__NR_socketpair),
#endif
#ifdef __NR_socketcall
      DENY_SYSCALL(__NR_socketcall),
#endif
#ifdef __NR_ptrace
      DENY_SYSCALL(__NR_ptrace),
#endif
#ifdef __NR_process_vm_readv
      DENY_SYSCALL(__NR_process_vm_readv),
#endif
#ifdef __NR_process_vm_writev
      DENY_SYSCALL(__NR_process_vm_writev),
#endif
#ifdef __NR_pidfd_getfd
      DENY_SYSCALL(__NR_pidfd_getfd),
#endif
#ifdef __NR_pidfd_send_signal
      DENY_SYSCALL(__NR_pidfd_send_signal),
#endif
#ifdef __NR_kcmp
      DENY_SYSCALL(__NR_kcmp),
#endif
#ifdef __NR_pidfd_open
      DENY_SYSCALL(__NR_pidfd_open),
#endif
#ifdef __NR_process_madvise
      DENY_SYSCALL(__NR_process_madvise),
#endif
#ifdef __NR_process_mrelease
      DENY_SYSCALL(__NR_process_mrelease),
#endif
#ifdef __NR_io_uring_setup
      DENY_SYSCALL(__NR_io_uring_setup),
#endif
#ifdef __NR_io_uring_enter
      DENY_SYSCALL(__NR_io_uring_enter),
#endif
#ifdef __NR_io_uring_register
      DENY_SYSCALL(__NR_io_uring_register),
#endif
#ifdef __NR_bpf
      DENY_SYSCALL(__NR_bpf),
#endif
#ifdef __NR_userfaultfd
      DENY_SYSCALL(__NR_userfaultfd),
#endif
#ifdef __NR_perf_event_open
      DENY_SYSCALL(__NR_perf_event_open),
#endif
#ifdef __NR_keyctl
      DENY_SYSCALL(__NR_keyctl),
#endif
#ifdef __NR_add_key
      DENY_SYSCALL(__NR_add_key),
#endif
#ifdef __NR_request_key
      DENY_SYSCALL(__NR_request_key),
#endif
#ifdef __NR_open_by_handle_at
      DENY_SYSCALL(__NR_open_by_handle_at),
#endif
#ifdef __NR_name_to_handle_at
      DENY_SYSCALL(__NR_name_to_handle_at),
#endif
      /*
       * Landlock does not currently mediate file metadata changes.  The
       * verifier runtime never needs to alter ownership, modes, timestamps,
       * or extended attributes, including inside the disposable workspace.
       * Deny them here so a read-only Landlock rule is read-only in the
       * ordinary and metadata senses.
       */
#ifdef __NR_chmod
      DENY_SYSCALL(__NR_chmod),
#endif
#ifdef __NR_fchmod
      DENY_SYSCALL(__NR_fchmod),
#endif
#ifdef __NR_fchmodat
      DENY_SYSCALL(__NR_fchmodat),
#endif
#ifdef __NR_fchmodat2
      DENY_SYSCALL(__NR_fchmodat2),
#endif
#ifdef __NR_chown
      DENY_SYSCALL(__NR_chown),
#endif
#ifdef __NR_fchown
      DENY_SYSCALL(__NR_fchown),
#endif
#ifdef __NR_lchown
      DENY_SYSCALL(__NR_lchown),
#endif
#ifdef __NR_fchownat
      DENY_SYSCALL(__NR_fchownat),
#endif
#ifdef __NR_utime
      DENY_SYSCALL(__NR_utime),
#endif
#ifdef __NR_utimes
      DENY_SYSCALL(__NR_utimes),
#endif
#ifdef __NR_futimesat
      DENY_SYSCALL(__NR_futimesat),
#endif
#ifdef __NR_utimensat
      DENY_SYSCALL(__NR_utimensat),
#endif
#ifdef __NR_setxattr
      DENY_SYSCALL(__NR_setxattr),
#endif
#ifdef __NR_lsetxattr
      DENY_SYSCALL(__NR_lsetxattr),
#endif
#ifdef __NR_fsetxattr
      DENY_SYSCALL(__NR_fsetxattr),
#endif
#ifdef __NR_removexattr
      DENY_SYSCALL(__NR_removexattr),
#endif
#ifdef __NR_lremovexattr
      DENY_SYSCALL(__NR_lremovexattr),
#endif
#ifdef __NR_fremovexattr
      DENY_SYSCALL(__NR_fremovexattr),
#endif
#ifdef __NR_mount
      DENY_SYSCALL(__NR_mount),
#endif
#ifdef __NR_umount2
      DENY_SYSCALL(__NR_umount2),
#endif
#ifdef __NR_pivot_root
      DENY_SYSCALL(__NR_pivot_root),
#endif
#ifdef __NR_chroot
      DENY_SYSCALL(__NR_chroot),
#endif
#ifdef __NR_unshare
      DENY_SYSCALL(__NR_unshare),
#endif
#ifdef __NR_setns
      DENY_SYSCALL(__NR_setns),
#endif
#ifdef __NR_fsopen
      DENY_SYSCALL(__NR_fsopen),
#endif
#ifdef __NR_fsconfig
      DENY_SYSCALL(__NR_fsconfig),
#endif
#ifdef __NR_fsmount
      DENY_SYSCALL(__NR_fsmount),
#endif
#ifdef __NR_fspick
      DENY_SYSCALL(__NR_fspick),
#endif
#ifdef __NR_move_mount
      DENY_SYSCALL(__NR_move_mount),
#endif
#ifdef __NR_open_tree
      DENY_SYSCALL(__NR_open_tree),
#endif
#ifdef __NR_mount_setattr
      DENY_SYSCALL(__NR_mount_setattr),
#endif
#ifdef __NR_memfd_create
      DENY_SYSCALL(__NR_memfd_create),
#endif
#ifdef __NR_mmap
      /* File-backed executable mappings are needed by the dynamic loader.
       * Anonymous executable memory is not needed by Lean proof checking. */
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_mmap, 0, 5),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
               offsetof(struct seccomp_data, args[2])),
      BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, PROT_EXEC, 0, 3),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
               offsetof(struct seccomp_data, args[3])),
      BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, MAP_ANONYMOUS, 0, 1),
      BPF_STMT(BPF_RET | BPF_K,
               SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_mprotect
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_mprotect, 0, 3),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
               offsetof(struct seccomp_data, args[2])),
      BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, PROT_EXEC, 0, 1),
      BPF_STMT(BPF_RET | BPF_K,
               SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_pkey_mprotect
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_pkey_mprotect, 0, 3),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
               offsetof(struct seccomp_data, args[2])),
      BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, PROT_EXEC, 0, 1),
      BPF_STMT(BPF_RET | BPF_K,
               SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_clone3
      /* ENOSYS makes standard runtimes fall back to clone(2), filtered below. */
      REJECT_SYSCALL(__NR_clone3, ENOSYS),
#endif
#ifdef __NR_clone
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone, 0, 3),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
               offsetof(struct seccomp_data, args[0])),
      BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K,
               CLONE_NEWCGROUP | CLONE_NEWIPC | CLONE_NEWNET | CLONE_NEWNS |
                   CLONE_NEWPID | CLONE_NEWTIME | CLONE_NEWUSER | CLONE_NEWUTS |
                   CLONE_PARENT,
               0, 1),
      BPF_STMT(BPF_RET | BPF_K,
               SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_shmget
      DENY_SYSCALL(__NR_shmget),
#endif
#ifdef __NR_shmat
      DENY_SYSCALL(__NR_shmat),
#endif
#ifdef __NR_shmdt
      DENY_SYSCALL(__NR_shmdt),
#endif
#ifdef __NR_shmctl
      DENY_SYSCALL(__NR_shmctl),
#endif
#ifdef __NR_msgget
      DENY_SYSCALL(__NR_msgget),
#endif
#ifdef __NR_msgsnd
      DENY_SYSCALL(__NR_msgsnd),
#endif
#ifdef __NR_msgrcv
      DENY_SYSCALL(__NR_msgrcv),
#endif
#ifdef __NR_msgctl
      DENY_SYSCALL(__NR_msgctl),
#endif
#ifdef __NR_semget
      DENY_SYSCALL(__NR_semget),
#endif
#ifdef __NR_semop
      DENY_SYSCALL(__NR_semop),
#endif
#ifdef __NR_semtimedop
      DENY_SYSCALL(__NR_semtimedop),
#endif
#ifdef __NR_semctl
      DENY_SYSCALL(__NR_semctl),
#endif
#ifdef __NR_setpriority
      DENY_SYSCALL(__NR_setpriority),
#endif
#ifdef __NR_ioprio_set
      DENY_SYSCALL(__NR_ioprio_set),
#endif
#ifdef __NR_sched_setaffinity
      DENY_SYSCALL(__NR_sched_setaffinity),
#endif
#ifdef __NR_prlimit64
      /* Self-only resource-limit queries/changes remain available. */
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_prlimit64, 0, 4),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
               offsetof(struct seccomp_data, args[0])),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0, 1, 0),
      BPF_STMT(BPF_RET | BPF_K,
               SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_setsid
      DENY_SYSCALL(__NR_setsid),
#endif
#ifdef __NR_setpgid
      DENY_SYSCALL(__NR_setpgid),
#endif
#ifdef __NR_kill
      DENY_SYSCALL(__NR_kill),
#endif
#ifdef __NR_tkill
      DENY_SYSCALL(__NR_tkill),
#endif
#ifdef __NR_tgkill
      DENY_SYSCALL(__NR_tgkill),
#endif
#ifdef __NR_rt_sigqueueinfo
      DENY_SYSCALL(__NR_rt_sigqueueinfo),
#endif
#ifdef __NR_rt_tgsigqueueinfo
      DENY_SYSCALL(__NR_rt_tgsigqueueinfo),
#endif
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
  };
  struct sock_fprog program = {
      .len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
      .filter = filter,
  };
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
    return -1;
  }
  return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program);
}

int main(int argc, char **argv) {
  if (require_landlock() != 0) {
    return 126;
  }
  if (argc == 2 && strcmp(argv[1], "--check-landlock") == 0) {
    return 0;
  }
  if (argc < 2) {
    fputs("usage: seccomp-launcher COMMAND [ARG ...]\n", stderr);
    return 64;
  }
  if (install_filter() != 0) {
    perror("cannot install seccomp filter");
    return 126;
  }
  execvp(argv[1], &argv[1]);
  perror("cannot execute sandboxed command");
  return 126;
}
