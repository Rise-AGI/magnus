# 仓库传输

默认情况下，Slurm 使用 GitHub SSH 地址，local 使用 HTTPS；Git 已有的
`insteadOf` 规则和 credential helper 仍然生效。

配置 `execution.github_https_url`，可让仓库准备和分支／提交查询统一通过可信的
Git Smart HTTP 代理：

```yaml
execution:
  github_https_url: https://github-proxy.example.org
```

地址只能是 HTTPS origin，不能包含凭据、路径、查询参数或 fragment。认证由 Git
管理：为服务账号配置对应代理域名的 credential helper，代理须安全转发 GitHub
认证信息。SSH 格式地址改写为 HTTPS 后**不会使用 SSH 私钥**；私有仓库必须由
有访问权限的 HTTPS 身份读取。认证或代理报错时，不自动回退直连 SSH。

该配置也控制已有仓库缓存后续的 fetch，无需改写缓存 origin 或清除对象，保留
现有分支 refspec。完整不可变提交已命中缓存时仍不访问网络。无需数据库迁移、
SDK 更新或镜像重建。该配置不控制 OCI registry mirror 或 BuildKit 出网。

启用前，用服务账号的 HTTPS 身份验证公开仓库及实际需要的私有仓库。正常重启
控制面以加载配置；准备过程持锁时不能外部改写或删除缓存。
