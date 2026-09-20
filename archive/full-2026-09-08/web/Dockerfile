FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
# No lockfile is committed yet, so install resolves fresh. `npm ci` takes over once
# package-lock.json is checked in.
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY tcg-app.conf /etc/nginx/tcg-app.conf
COPY tls.conf.template /etc/nginx/tls.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN mkdir -p /etc/nginx/certs
EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
