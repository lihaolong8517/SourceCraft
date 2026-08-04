# 必须在任何其他代码之前执行，确保 stdin/stdout/stderr 有效
if __name__ == '__main__':
    is_frozen = getattr(sys, 'frozen', False)
    if is_frozen and sys.platform == 'win32':
        import io
        def fix_std_handle(handle_name, fd, mode):
            try:
                handle = getattr(sys, handle_name)
                # 如果句柄是 None 或无效，尝试恢复或替换
                if handle is None:
                    try:
                        # 尝试从 FD 恢复（适用于子进程 Pipe 继承）
                        setattr(sys, handle_name, os.fdopen(fd, mode, buffering=1, encoding='utf-8'))
                    except:
                        # 失败则说明没有关联的 Pipe 或 Console，回退到 devnull
                        setattr(sys, handle_name, open(os.devnull, mode))
                else:
                    try:
                        handle.fileno()
                    except (OSError, io.UnsupportedOperation, AttributeError):
                        try:
                            handle.close()
                        except: pass
                        try:
                            setattr(sys, handle_name, os.fdopen(fd, mode, buffering=1, encoding='utf-8'))
                        except:
                            setattr(sys, handle_name, open(os.devnull, mode))
            except Exception:
                try:
                    setattr(sys, handle_name, io.StringIO('') if 'r' in mode else io.StringIO())
                except: pass

        fix_std_handle('stderr', 2, 'w')
        fix_std_handle('stdout', 1, 'w')
        # 关键：打包子进程命令通道依赖 stdin（用于 COOKIES/PING 等指令）
        # 部分机器在 windowed frozen 下 stdin 句柄可能无效，需显式修复
        fix_std_handle('stdin', 0, 'r')

    # === 【关键】环境修复 ===
    # 确保在启动任何功能前，PLAYWRIGHT_BROWSERS_PATH 等环境变量已正确设置
    playwright_fix.setup_playwright_environment()
    playwright_fix.fix_windows_path_env()


if __name__ == '__main__':
    # 如果是 playwright 启动子进程，直接运行 launcher 逻辑并退出
    if len(sys.argv) > 1 and sys.argv[1] == "--playwright-launcher":
        try:
            launcher_script = sys.argv[2] if len(sys.argv) > 2 else ""
            if launcher_script and os.path.isfile(launcher_script):
                import runpy

                # 统一开发态与打包态：都执行同一份 _launcher.py
                script_argv = [launcher_script] + sys.argv[3:]
                old_argv = sys.argv
                try:
                    sys.argv = script_argv
                    runpy.run_path(launcher_script, run_name="__main__")
                finally:
                    sys.argv = old_argv
            else:
                # 兼容回退：若脚本不存在，继续使用内置 launcher
                _load_playwright_browser_module().run_launcher()
        except Exception as e:
            try:
                print(f"[BROWSER] ERROR: BROWSER_UNKNOWN|启动子进程失败: {e}", flush=True)
            except:
                pass
        sys.exit(0)
        
    multiprocessing.freeze_support()


# Launch the application
if __name__ == '__main__':
    main()
