# 使用輕量級的 Alpine Linux Nginx 映像檔
FROM nginx:alpine

# 移除預設的 Nginx 設定檔
RUN rm /etc/nginx/conf.d/default.conf

# 建立 ssl 資料夾
RUN mkdir -p /etc/nginx/ssl

# (可選) 在 Build 階段複製設定檔與憑證進入容器內
# 這樣部署時，yaml 就不需要寫 volumes 掛載
# COPY nginx.conf /etc/nginx/conf.d/default.conf
# COPY ssl/ /etc/nginx/ssl/

# 暴露 HTTP 與 HTTPS prot
EXPOSE 80
EXPOSE 443

# Nginx alpine 版本已預設啟動指令
